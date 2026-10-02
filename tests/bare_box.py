#!/usr/bin/env python3
"""Offline bare-box, backup and repository selection guards."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import image
import host_common as h
import backup
import boxes
STORAGE_CHECK = backup.storage
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/chart-box/scripts'))
import policy
spec = importlib.util.spec_from_file_location('bare_sync', Path(__file__).resolve().parents[1] / 'skills/chart-box/scripts/sync.py')
sync = importlib.util.module_from_spec(spec); spec.loader.exec_module(sync)


class BareBox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        home = patch.object(backup, 'BACKUP_HOME', self.root / 'ssd-backups'); home.start(); self.addCleanup(home.stop)
        storage = patch.object(backup, 'storage', return_value=1024**3); storage.start(); self.addCleanup(storage.stop)

    def test_tracking_controls_private_names_not_contents(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for name in ('.env.local', '.env.sample', '.env.production.example', 'private.pem', '.npmrc', 'index.ts'):
            (self.root / name).write_text('synthetic')
        subprocess.run(['git', '-C', str(self.root), 'add', '.env.local', 'private.pem'], check=True)
        exceptions = policy.exceptions(self.root)
        self.assertEqual(exceptions, ['.env.local', 'private.pem'])
        files = policy.manifest(self.root, exceptions)
        self.assertIn('.env.local', files); self.assertIn('private.pem', files)
        self.assertIn('index.ts', files)  # Ordinary new code is not staged in Git.
        self.assertIn('.env.production.example', files); self.assertNotIn('.npmrc', files)
        subprocess.run(['git', '-C', str(self.root), 'rm', '--cached', '-q', '.env.local'], check=True)
        self.assertNotIn('.env.local', policy.manifest(self.root, policy.exceptions(self.root)))

    def test_untracked_private_siblings_and_dependencies_are_excluded(self):
        for name in ('.env.d/tracked', '.env.d/untracked', 'node_modules/private.pem', 'keys/a.pem', 'keys/b.pem'):
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
        files = policy.manifest(self.root, ['.env.d/tracked', 'keys/a.pem', 'node_modules/private.pem'])
        self.assertEqual(set(files), {'.env.d/tracked', 'keys/a.pem'})

    def test_symlinks_do_not_escape_source(self):
        (self.root / 'link').symlink_to('/etc/passwd')
        self.assertEqual(policy.manifest(self.root, []), {})

    def test_checksum_mismatch_refuses_nvm(self):
        for name in image.PINS['nvm']['files']: (self.root / name).write_text('wrong')
        with self.assertRaisesRegex(RuntimeError, 'differs'): image.artifacts(self.root)

    def test_names_cannot_target_live_box(self):
        with self.assertRaises(RuntimeError): image.names('../sean-dev-pilot')
        self.assertEqual(image.names('test')[0], 'chart-bare-test')

    def test_existing_data_refused_before_create(self):
        (self.root / 'test').mkdir()
        with patch.object(image.p, 'capacity', return_value={'review': False}), patch.object(image.p, 'query', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'Retained data'):
                image.create({'boxes_root': str(self.root), 'project': 'p', 'machine_id': 'm', 'hdd_uuid': 'u'}, 'test', 'fp', None, None)

    def test_readiness_waits_for_systemd_and_bus(self):
        with patch.object(h, 'guest') as guest:
            h.wait_ready({}, 'test')
        argv = guest.call_args.args[2]
        self.assertEqual(argv[:2], ['timeout', '90'])
        for name, body in {
            'systemctl': 'if test -e "$FIXTURE/systemd"; then echo degraded; exit 1; fi; touch "$FIXTURE/systemd"; echo starting; exit 1',
            'timedatectl': 'if test -e "$FIXTURE/bus"; then echo Asia/Kuala_Lumpur; exit 0; fi; touch "$FIXTURE/bus"; exit 1',
            'sleep': 'exit 0',
        }.items():
            script = self.root / name; script.write_text('#!/bin/sh\n' + body + '\n'); script.chmod(0o755)
        result = subprocess.run(argv, env={**os.environ, 'FIXTURE': str(self.root),
                                'PATH': str(self.root) + ':' + os.environ['PATH']}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.root / 'systemd').exists()); self.assertTrue((self.root / 'bus').exists())

    def test_failed_readiness_does_not_start_identity_writes(self):
        with patch.object(h, 'guest', side_effect=RuntimeError('timeout')) as guest, patch.object(h, 'push') as push:
            with self.assertRaisesRegex(RuntimeError, 'readiness failed'):
                image.provision({}, 'test', None, None)
        self.assertEqual(guest.call_count, 1); push.assert_not_called()

    @contextlib.contextmanager
    def resume_fixture(self, state='fresh', *, base='fp'):
        builder, tests = image.names('test')
        directory = self.root / 'bare-images/test'; scripts = directory / 'scripts'; scripts.mkdir(parents=True)
        for name in image.FILES: (scripts / name).write_text('snapshot')
        record = {'owner': h.OWNER, 'build': 'test', 'builder': builder, 'tests': tests,
                  'commit': 'original', 'phase': 'acceptance-pending', 'fingerprint': 'fp'}
        (directory / 'build.json').write_text(json.dumps(record))
        c = {'project': 'p'}
        with patch.object(image.p, 'STATE', self.root), patch.object(image.p, 'config', return_value=c), \
             patch.object(image.p, 'check_host'), patch.object(image.p, 'locked', contextlib.nullcontext), \
             patch.object(h, 'host'), patch.object(h, 'candidate', return_value={'properties': {'chart.build': 'test', 'chart.commit': 'original'}}), \
             patch.object(image.p, 'public_key'), patch.object(image.p, 'run', return_value='snapshot'), \
             patch.object(image.p, 'query', return_value=[{'name': tests[0]}]), \
             patch.object(h, 'owned', return_value={'status': 'Running', 'config': {'volatile.base_image': base}}), \
             patch.object(h, 'wait_ready'), patch.object(h, 'guest', return_value=state), \
             patch.object(image, 'provision', return_value='') as provision, patch.object(image, 'create') as create, \
             contextlib.redirect_stdout(io.StringIO()):
            yield SimpleNamespace(config='fixture', build='test', apply=True), provision, create, scripts, record

    def test_resume_reuses_published_image_and_original_scripts(self):
        with self.resume_fixture() as (args, provision, create, scripts, record):
            image.resume(args)
            provision.assert_called_once_with({'project': 'p'}, record['tests'][0], scripts.parent / 'authorized_key', scripts)
            create.assert_called_once_with({'project': 'p'}, record['tests'][1], 'fp', scripts.parent / 'authorized_key', scripts)
            self.assertEqual(json.loads((scripts.parent / 'build.json').read_text()), record)

    def test_resume_does_not_reprovision_prepared_identity(self):
        with self.resume_fixture('prepared') as (args, provision, create, _, _):
            image.resume(args)
            provision.assert_not_called(); create.assert_called_once()

    def test_resume_refuses_partial_identity(self):
        with self.resume_fixture('partial') as (args, provision, create, _, _):
            with self.assertRaisesRegex(RuntimeError, 'Partial identity'): image.resume(args)
            provision.assert_not_called(); create.assert_not_called()

    def test_resume_refuses_replaced_test_instance(self):
        with self.resume_fixture(base='foreign') as (args, provision, create, _, _):
            with self.assertRaisesRegex(RuntimeError, 'Wrong test image'): image.resume(args)
            provision.assert_not_called(); create.assert_not_called()

    def test_resume_refuses_changed_snapshot(self):
        with self.resume_fixture() as (args, provision, create, scripts, _):
            (scripts / image.FILES[0]).write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'script differs'): image.resume(args)
            provision.assert_not_called(); create.assert_not_called()

    def test_restore_checks_contents_and_refuses_overwrite(self):
        source = self.root / 'backup'; source.mkdir()
        with tarfile.open(source / 'data.tar', 'w') as archive:
            item = tarfile.TarInfo('data'); item.size = 5; archive.addfile(item, io.BytesIO(b'hello'))
        record = {'owner': backup.OWNER, 'complete': True, 'box': 'test', 'data_sha256': image.p.digest(source / 'data.tar')}
        (source / 'backup.json').write_text(json.dumps(record))
        destination = self.root / 'scratch'
        self.assertEqual(backup.restore(source, destination)['box'], 'test')
        self.assertEqual((destination / 'data').read_text(), 'hello')
        with self.assertRaisesRegex(RuntimeError, 'must not exist'): backup.restore(source, destination)

    def test_restore_refuses_escaping_links(self):
        item = tarfile.TarInfo('link'); item.type = tarfile.SYMTYPE; item.linkname = '/etc'
        with self.assertRaises(tarfile.FilterError): backup.archive_filter(item, str(self.root))
        item = tarfile.TarInfo('../escape')
        with self.assertRaises(tarfile.FilterError): backup.archive_filter(item, str(self.root))

    def test_restore_rejects_corruption(self):
        source = self.root / 'backup'; source.mkdir()
        (source / 'data.tar').write_bytes(b'bad')
        (source / 'backup.json').write_text(json.dumps({'owner': backup.OWNER, 'complete': True, 'data_sha256': 'wrong'}))
        with self.assertRaisesRegex(RuntimeError, 'checksum'): backup.restore(source, self.root / 'scratch')

    def test_failed_backup_restarts_previously_running_box(self):
        c = {'hdd_mount': str(self.root), 'boxes_root': str(self.root / 'boxes'), 'project': 'p', 'machine_id': 'm', 'hdd_uuid': 'u'}
        calls = []
        def run(args):
            calls.append(args)
            return '1\tfixture' if args[0] == 'du' else ''
        with patch.object(h, 'owned', return_value={'status': 'Running'}), patch.object(h, 'instance', return_value={'status': 'Stopped'}), patch.object(image.p, 'run', side_effect=run), patch.object(backup, 'copy_command', side_effect=RuntimeError('copy failed')):
            with self.assertRaisesRegex(RuntimeError, 'copy failed'): backup.backup(c, 'test')
        self.assertEqual(calls[-1][:3], ['incus', 'start', 'local:test'])
        receipt = next(backup.backup_root(c).glob('*/test/backup.json'))
        self.assertFalse(json.loads(receipt.read_text())['complete'])

    def test_real_stopped_copy_and_scratch_restore(self):
        c = {'hdd_mount': str(self.root), 'boxes_root': str(self.root / 'boxes'), 'project': 'p', 'machine_id': 'm', 'hdd_uuid': 'u'}
        data = self.root / 'boxes/test'; data.mkdir(parents=True)
        (data / 'fixture').write_text('retained token')
        with patch.object(h, 'owned', return_value={'status': 'Stopped'}), patch.object(h, 'instance', return_value={'status': 'Stopped'}):
            saved = backup.backup(c, 'test')
        result = backup.restore(saved, self.root / 'scratch')
        self.assertEqual((self.root / 'scratch/fixture').read_text(), 'retained token')
        self.assertEqual(result['box'], 'test')

    def test_failed_generic_gate_does_not_mark_image_verified(self):
        directory = self.root / 'bare-images/test'; directory.mkdir(parents=True)
        record = {'owner': h.OWNER, 'phase': 'acceptance-pending', 'fingerprint': 'fp', 'tests': ['test-a', 'test-b']}
        path = directory / 'build.json'; path.write_text(json.dumps(record))
        from types import SimpleNamespace
        def guest(c, name, args):
            if args[0] == 'tailscale': return json.dumps({'BackendState': 'Running'})
            raise RuntimeError('generic gate failed')
        with patch.object(image.p, 'STATE', self.root), patch.object(image.p, 'config', return_value={}), \
             patch.object(image.p, 'check_host'), patch.object(image.p, 'locked', contextlib.nullcontext), \
             patch.object(h, 'host'), patch.object(h, 'candidate'), \
             patch.object(h, 'owned', return_value={'config': {'volatile.base_image': 'fp'}}), patch.object(h, 'guest', side_effect=guest):
            with self.assertRaisesRegex(RuntimeError, 'generic gate failed'):
                image.verify(SimpleNamespace(config='fixture', build='test', apply=True))
        self.assertEqual(json.loads(path.read_text()), record)

    def test_retention_keeps_manual_and_incomplete_generations(self):
        c = {'hdd_mount': str(self.root), 'machine_id': 'm', 'hdd_uuid': 'u'}
        for stamp, nightly, complete in [('20000101T000000.000000Z', False, True), ('20000102T000000.000000Z', True, False), ('20000103T000000.000000Z', True, True), ('29990101T000000.000000Z', True, True)]:
            p = backup.backup_root(c) / stamp / 'test'; p.mkdir(parents=True)
            (p / 'data.tar').write_bytes(b'fixture')
            (p / 'backup.json').write_text(json.dumps({'owner': backup.OWNER, 'complete': complete, 'nightly': nightly, 'machine_id': 'm', 'hdd_uuid': 'u'}))
        backup.prune(c, 7)
        self.assertEqual({p.name for p in backup.backup_root(c).iterdir()}, {'20000101T000000.000000Z', '20000102T000000.000000Z', '29990101T000000.000000Z'})

    def test_tracking_change_pauses_before_flush(self):
        cfg = {'records': {'repo': {'source': '/fixture', 'tracked': []}}}
        with patch.object(policy, 'exceptions', return_value=['.env.local']), patch.object(sync, 'owned', return_value={'identifier': 'owned', 'paused': False}), patch.object(sync, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'session paused'): sync.check_policy(None, cfg, 'repo')
            run.assert_called_once_with(None, 'sync', 'pause', 'owned')

    def test_nightly_excludes_managed_pilot_and_builder(self):
        with patch.object(backup, 'enrollments', return_value=['personal']), patch.object(h, 'owned', return_value={'config': {'user.chart-box': h.OWNER}}), patch.object(backup, 'backup', return_value='fixture-copy') as copy, patch.object(backup, 'prune'):
            backup.scheduled({'project': 'test'})
            copy.assert_called_once_with({'project': 'test'}, 'personal', nightly=True)

    def test_no_enrollment_means_no_automatic_stops(self):
        with patch.object(image.p, 'STATE', self.root), patch.object(backup, 'backup') as copy, patch.object(backup, 'prune'):
            backup.scheduled({'project': 'test'})
            copy.assert_not_called()

    def test_test_boxes_and_pilot_cannot_enroll(self):
        for name in ('sean-dev-pilot', 'chart-test-example-a', 'chart-bare-example', 'retained-123'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'cannot join'):
                backup.enrollment({}, name)

    def test_enroll_and_unenroll_do_not_mutate_box(self):
        c = {'machine_id': 'm'}
        with patch.object(image.p, 'STATE', self.root), patch.object(h, 'owned', return_value={'config': {'user.chart-box': h.OWNER}}), patch.object(image.p, 'run') as run:
            backup.enrollment(c, 'alex-dev'); backup.enrollment(c, 'alex-dev')
            self.assertEqual(backup.enrollments(c), ['alex-dev'])
            backup.enrollment(c, 'alex-dev', remove=True)
            self.assertEqual(backup.enrollments(c), [])
            run.assert_not_called()

    def test_destination_cannot_share_hdd_device(self):
        with self.assertRaisesRegex(RuntimeError, 'separate from the HDD'):
            STORAGE_CHECK({'hdd_mount': '/'}, self.root)

    def test_capacity_preserves_quarter_of_ssd(self):
        with patch.object(backup.p, 'no_symlinks'), patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'stat', autospec=True, side_effect=lambda path: SimpleNamespace(st_dev=2 if path == Path('/hdd') else 1)), \
             patch.object(backup.shutil, 'disk_usage', return_value=SimpleNamespace(total=100 * 1024**3, free=26 * 1024**3)):
            with self.assertRaisesRegex(RuntimeError, 'Insufficient SSD'):
                STORAGE_CHECK({'hdd_mount': '/hdd'}, self.root)

    def test_capacity_failure_does_not_stop_box(self):
        with patch.object(h, 'owned', return_value={'status': 'Running'}), patch.object(backup, 'storage', side_effect=RuntimeError('Insufficient SSD')), patch.object(image.p, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Insufficient SSD'):
                backup.backup({}, 'test')
            run.assert_not_called()

    def test_copy_ceiling_retains_partial_archive(self):
        target = self.root / 'partial'
        with patch.object(backup, 'storage', return_value=1024):
            with self.assertRaisesRegex(RuntimeError, 'partial archive retained'):
                backup.copy_command({}, [sys.executable, '-c', 'import sys; f = open(sys.argv[1], "wb"); f.write(b"x" * 8192); f.flush()', target])
        self.assertTrue(target.exists()); self.assertLessEqual(target.stat().st_size, 1024)

    @contextlib.contextmanager
    def recreation_fixture(self, *, restored=True, changed_identity=False):
        hashes = iter(['original-ssh', 'changed-ssh' if changed_identity else 'original-ssh'])
        def guest(c, name, args):
            if args[0] == 'cat': return 'ssh-ed25519 fixture-key\n'
            if args[0] == 'sha256sum': return next(hashes)
            if args[0] == 'tailscale': return json.dumps({'Self': {'ID': 'same-node'}, 'BackendState': 'Running'})
            raise AssertionError(args)
        with patch.object(image.p, 'STATE', self.root), patch.object(h, 'owned', return_value={'status': 'Running'}), \
             patch.object(h, 'guest', side_effect=guest), patch.object(backup, 'backup', return_value=self.root / 'archive') as copy, \
             patch.object(backup, 'restore', side_effect=None if restored else RuntimeError('restore failed'), return_value={'scratch': str(backup.scratch_root() / 'fixture')}) as restore, \
             patch.object(h, 'instance', return_value={'status': 'Stopped'}), patch.object(image.p, 'run') as run, \
             patch.object(image, 'create') as create, contextlib.redirect_stdout(io.StringIO()):
            yield copy, restore, run, create

    def test_recreation_restore_failure_does_not_detach_original(self):
        with self.recreation_fixture(restored=False) as (_, _, run, create):
            with self.assertRaisesRegex(RuntimeError, 'restore failed'):
                boxes.recreate({'project': 'p'}, 'test-box', 'fp', self.root)
            run.assert_not_called(); create.assert_not_called()

    def test_recreation_uses_ssd_scratch_and_preserves_node_identity(self):
        c = {'project': 'p'}
        with self.recreation_fixture() as (copy, restore, run, create):
            record = boxes.recreate(c, 'test-box', 'fp', self.root)
            copy.assert_called_once_with(c, 'test-box', rootfs=True, resume=False)
            self.assertEqual(restore.call_args.args[1].parent, backup.scratch_root())
            self.assertEqual(restore.call_args.args[2], c)
            self.assertTrue(create.call_args.kwargs['adopt'])
            self.assertEqual(record['phase'], 'recreated')
            self.assertEqual(record['identity']['tailscale_id'], 'same-node')
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ['config', 'move'])

    def test_recreation_identity_change_leaves_failure_receipt(self):
        with self.recreation_fixture(changed_identity=True):
            with self.assertRaisesRegex(RuntimeError, 'SSH identity differs'):
                boxes.recreate({'project': 'p'}, 'test-box', 'fp', self.root)
        record = json.loads(next((self.root / 'recreations').glob('*/recreate.json')).read_text())
        self.assertEqual(record['phase'], 'original-retained')

    def test_refresh_recovers_after_old_session_termination(self):
        from types import SimpleNamespace
        rec = {'name': 'fixture', 'owner': 'ours', 'source': '/laptop', 'tracked': [],
               'pending_tracked': ['tracked.pem'], 'phase': 'refreshing'}
        cfg = {'records': {'repo': rec}, 'sessions': {'repo': 'old'}}
        with patch.object(policy, 'exceptions', return_value=['tracked.pem']), patch.object(sync, 'sessions', return_value=[]), \
             patch.object(sync, 'run') as run, patch.object(sync, 'setup') as setup:
            sync.refresh(SimpleNamespace(repo='repo'), cfg, self.root / 'box.json')
            run.assert_not_called(); setup.assert_called_once()
        self.assertEqual(rec['tracked'], ['tracked.pem'])
        self.assertTrue(rec['refreshing'])
        self.assertNotIn('repo', cfg['sessions'])

    def test_session_endpoint_substitution_refused(self):
        rec = {'name': 'fixture', 'owner': 'ours', 'source': '/laptop', 'tracked': []}
        session = {'name': 'fixture', 'labels': {'chart-owner': 'ours'}, 'alpha': {'protocol': 'local', 'path': '/other'}, 'beta': {'protocol': 'ssh', 'user': 'root', 'host': 'box', 'path': '/srv/chart/source/repo'}}
        with self.assertRaisesRegex(RuntimeError, 'endpoints'): sync.validate({'ssh': 'root@box', 'source_root': '/srv/chart/source'}, 'repo', rec, session)


if __name__ == '__main__': unittest.main()

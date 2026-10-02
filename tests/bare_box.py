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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import image
import host_common as h
import backup
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/chart-box/scripts'))
import policy
spec = importlib.util.spec_from_file_location('bare_sync', Path(__file__).resolve().parents[1] / 'skills/chart-box/scripts/sync.py')
sync = importlib.util.module_from_spec(spec); spec.loader.exec_module(sync)


class BareBox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

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
            if args[0] == 'tar': raise RuntimeError('copy failed')
            return ''
        with patch.object(h, 'owned', return_value={'status': 'Running'}), patch.object(h, 'instance', return_value={'status': 'Stopped'}), patch.object(image.p, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'copy failed'): backup.backup(c, 'test')
        self.assertEqual(calls[-1][:3], ['incus', 'start', 'local:test'])
        receipt = next((self.root / 'shared-dev/backups/boxes').glob('*/test/backup.json'))
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

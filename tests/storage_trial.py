#!/usr/bin/env python3
"""Offline refusal and isolation checks for the HDD storage trial."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'incus'))
spec = importlib.util.spec_from_file_location('trial', ROOT / 'incus/storage_trial.py')
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
spec = importlib.util.spec_from_file_location('syslog_trial', ROOT / 'tests/storage_syslog_live.py')
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)


class StorageTrial(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((ROOT / 'incus/host.example.json').read_text())
        self.c.update(machine_id='a' * 32, hdd_uuid='14ef1cfa-28ee-4986-8989-2e248896bf07')
        self.fp = 'b' * 64

    def test_names_cannot_select_developer_instance(self):
        for value in ('../live', 'live;id', '/live', 'a' * 13, ''):
            with self.subTest(value=value), self.assertRaises(RuntimeError): t.trial_names(value)
        self.assertEqual(t.trial_names('trial1'), ['chart-storage-test-trial1-ssd', 'chart-storage-test-trial1-hdd'])

    def test_fixture_has_no_data_or_identity_attachment(self):
        obj = t.fixture_spec(self.c, t.trial_names('trial1')[0], 'hdd', self.fp, 'trial1')
        self.assertEqual(set(obj['devices']), {'root', 'eth0'})
        self.assertEqual(obj['profiles'], [])
        self.assertEqual(obj['devices']['root']['pool'], 'hdd')
        self.assertEqual(obj['config']['security.privileged'], 'false')
        self.assertEqual(obj['config']['security.idmap.isolated'], 'true')
        self.assertNotIn('user.chart-box', obj['config'])

    def test_foreign_fixture_never_eligible_for_cleanup(self):
        expected = t.fixture_spec(self.c, 'chart-storage-test-trial1-hdd', 'hdd', self.fp, 'trial1')
        live = copy.deepcopy(expected); live['config']['volatile.base_image'] = self.fp
        for kind in ('pool', 'owner', 'privileged', 'mount', 'profile', 'base', 'extra'):
            obj = copy.deepcopy(live)
            if kind == 'pool': obj['devices']['root']['pool'] = 'other'
            if kind == 'owner': obj['config']['user.chart-storage-trial'] = 'other'
            if kind == 'privileged': obj['config']['security.privileged'] = 'true'
            if kind == 'mount': obj['devices']['data'] = {'type': 'disk', 'source': '/private', 'path': '/data'}
            if kind == 'profile': obj['profiles'] = ['default']
            if kind == 'base': obj['config']['volatile.base_image'] = 'c' * 64
            if kind == 'extra': obj['config']['raw.lxc'] = 'changed'
            with self.subTest(kind=kind), patch.object(t.h, 'instance', return_value=obj):
                with self.assertRaises(RuntimeError): t.validate_fixture(self.c, expected['name'], expected)

    def test_foreign_pool_refused(self):
        wanted = t.pool_spec(self.c)
        for kind in ('source', 'driver', 'owner'):
            obj = copy.deepcopy(wanted)
            if kind == 'source': obj['config']['source'] = '/home/someone'
            if kind == 'driver': obj['driver'] = 'btrfs'
            if kind == 'owner': obj['config']['user.chart-infra'] = 'foreign'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError): t.validate_pool(self.c, obj)

    def test_instance_collision_refused_before_pool_mutation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(t.p, 'STATE', Path(directory)), \
             patch.object(t.h, 'host'), patch.object(t.h, 'candidate'), \
             patch.object(t.p, 'query', return_value=[{'name': t.trial_names('trial1')[0]}]) as query:
            with self.assertRaisesRegex(RuntimeError, 'collision'): t.execute(self.c, 'trial1', self.fp)
            query.assert_called_once_with('/1.0/instances?project=chart-dev&recursion=1')
            self.assertFalse((Path(directory) / 'storage-trials').exists())

    def test_receipt_refuses_blind_rerun(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(t.p, 'STATE', Path(directory)), \
             patch.object(t.h, 'host'), patch.object(t.h, 'candidate'), patch.object(t.p, 'query') as query:
            (Path(directory) / 'storage-trials/trial1').mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, 'receipt exists'): t.execute(self.c, 'trial1', self.fp)
            query.assert_not_called()

    def test_unregistered_data_directory_never_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {**self.c, 'hdd_mount': directory}
            path = t.target(c); path.mkdir(parents=True); (path / 'keep').write_text('original')
            with patch.object(t.p, 'STATE', Path(directory) / 'state'), patch.object(t.h, 'host'), \
                 patch.object(t.h, 'candidate'), patch.object(t.p, 'capacity', return_value={'review': False}), \
                 patch.object(t.p, 'query', return_value=[]) as query:
                with self.assertRaisesRegex(RuntimeError, 'Unregistered'): t.execute(c, 'trial1', self.fp)
                self.assertTrue(all(len(call.args) == 1 for call in query.call_args_list))
            self.assertEqual((path / 'keep').read_text(), 'original')

    def test_plan_does_not_mutate(self):
        argv = ['storage_trial.py', '--config', '/fixture.json', '--trial', 'trial1', '--image', self.fp]
        with patch.object(sys, 'argv', argv), patch.object(t.p, 'config', return_value=self.c), \
             patch.object(t.p, 'check_host'), patch.object(t, 'execute') as execute, contextlib.redirect_stdout(io.StringIO()) as out:
            t.main(); execute.assert_not_called()
            self.assertFalse(json.loads(out.getvalue())['apply'])

    def test_original_failure_survives_cleanup_failure(self):
        record = {'phase': 'docker-build-run', 'results': {'hdd': {'files': 4096}}}
        original = RuntimeError('Docker build failed')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(t, 'validate_fixture', return_value={'status': 'Running'}), \
             patch.object(t.p, 'run', side_effect=RuntimeError('Shutdown timed out')) as run:
            with self.assertRaises(RuntimeError) as caught:
                with t.retain_failure(self.c, 'fixture', {}, Path(directory), record):
                    raise original
            self.assertIs(caught.exception, original)
            saved = json.loads((Path(directory) / 'proof.json').read_text())
            self.assertEqual(saved['failed_step'], 'docker-build-run')
            self.assertIn('Docker build failed', saved['error'])
            self.assertIn('Shutdown timed out', saved['cleanup_error'])
            self.assertEqual(saved['results']['hdd']['files'], 4096)
            run.assert_called_once()

    def test_failed_stop_is_not_retried(self):
        for phase in ('stopping-for-restart', 'stopping-for-removal'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory, \
                 patch.object(t, 'validate_fixture', return_value={'status': 'Running'}), \
                 patch.object(t.p, 'run') as run:
                record = {'phase': phase}
                with self.assertRaisesRegex(RuntimeError, 'timed out'):
                    with t.retain_failure(self.c, 'fixture', {}, Path(directory), record):
                        raise RuntimeError('Shutdown timed out')
                run.assert_not_called()
                self.assertEqual(record['failed_step'], phase)

    def test_foreign_fixture_is_retained_without_stop(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(t, 'validate_fixture', side_effect=RuntimeError('Ownership differs')), \
             patch.object(t.p, 'run') as run:
            record = {'phase': 'docker-build-run'}
            with self.assertRaisesRegex(RuntimeError, 'Original failure'):
                with t.retain_failure(self.c, 'fixture', {}, Path(directory), record):
                    raise RuntimeError('Original failure')
            run.assert_not_called()
            self.assertIn('Ownership differs', record['cleanup_error'])

    def test_checkpoint_persists_partial_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            record = {'results': {'hdd': {'write_and_fsync_seconds': 2}}}
            t.checkpoint(Path(directory), record, 'fixture', 'docker-build-run')
            saved = json.loads((Path(directory) / 'proof.json').read_text())
            self.assertEqual(saved['phase'], 'docker-build-run')
            self.assertEqual(saved['instance'], 'fixture')
            self.assertEqual(saved['results'], record['results'])


class SyslogExperiment(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            c = json.loads((ROOT / 'incus/host.example.json').read_text())
            c.update(machine_id='a' * 32, hdd_mount=str(root / 'hdd'))
            name = s.t.trial_names('trial1')[1]
            original = dict(owner=s.t.OWNER, host=c['machine_id'], trial='trial1', pool='hdd',
                            source=str(s.t.target(c)), image='b' * 64)
            proof = root / 'storage-trials/trial1/proof.json'
            s.h.save(proof, original)
            live = s.t.fixture_spec(c, name, 'hdd', original['image'], 'trial1')
            live['config']['volatile.base_image'] = original['image']
            live['status'] = 'Running'
            for obj, method in ((s.h, 'host'), (s.h, 'candidate'), (s.h, 'wait_ready'),
                                (s.t, 'validate_pool'), (s.p, 'query')):
                stack.enter_context(patch.object(obj, method))
            stack.enter_context(patch.object(s.p, 'STATE', root))
            stack.enter_context(patch.object(s.h, 'instance', side_effect=lambda *_: live))
            def command(args, **kwargs):
                if 'stop' in args: live['status'] = 'Stopped'
                if 'start' in args: live['status'] = 'Running'
                return ''
            run = stack.enter_context(patch.object(s.p, 'run', side_effect=command))
            def guest(c, name, args):
                if args[0] == 'cat' or (args[0] == 'python3' and args[2] == s.CHECK):
                    return json.dumps({'sha256': 'fixture-hash', 'syslog_fd_flags': ['0o2004002']})
                return 'fixture-output'
            guest_mock = stack.enter_context(patch.object(s, 'guest', side_effect=guest))
            yield c, proof, live, run, guest_mock

    def test_data_attachment_refuses_experiment(self):
        with self.fixture() as (c, proof, live, run, guest):
            live['devices']['data'] = {'type': 'disk', 'source': '/private', 'path': '/data'}
            with self.assertRaisesRegex(RuntimeError, 'devices'): s.execute(c, 'trial1')
            run.assert_not_called(); guest.assert_not_called()

    def test_foreign_receipt_refused(self):
        with self.fixture() as (c, proof, live, run, guest):
            original = json.loads(proof.read_text()); original['host'] = 'other'
            s.h.save(proof, original)
            with self.assertRaisesRegex(RuntimeError, 'identity'): s.execute(c, 'trial1')
            run.assert_not_called(); guest.assert_not_called()

    def test_existing_experiment_refuses_retry(self):
        with self.fixture() as (c, proof, live, run, guest):
            proof.with_name('syslog-experiment.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'receipt exists'): s.execute(c, 'trial1')
            run.assert_not_called(); guest.assert_not_called()

    def test_success_retains_fixture_and_original_receipt(self):
        with self.fixture() as (c, proof, live, run, guest), contextlib.redirect_stdout(io.StringIO()):
            original = proof.read_bytes()
            s.execute(c, 'trial1')
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(sum('--force' in cmd for cmd in calls), 1)
            self.assertEqual(sum('--timeout' in cmd for cmd in calls), 3)
            self.assertTrue(all('local:' + live['name'] in cmd for cmd in calls))
            self.assertFalse(any('delete' in cmd for cmd in calls))
            self.assertEqual(proof.read_bytes(), original)
            for call in guest.call_args_list:
                args = call.args[2]
                if args[:2] == ['python3', '-c']:
                    compile(args[2], '<guest-script>', 'exec')
            self.assertEqual(live['status'], 'Stopped')
            report = json.loads(proof.with_name('syslog-experiment.json').read_text())
            self.assertEqual(len(report['cycles']), 3)

    def test_failed_graceful_stop_has_no_force_fallback(self):
        with self.fixture() as (c, proof, live, run, guest):
            normal = run.side_effect
            def command(args, **kwargs):
                if '--timeout' in args: raise RuntimeError('stop timed out')
                return normal(args, **kwargs)
            run.side_effect = command
            with self.assertRaisesRegex(RuntimeError, 'stop timed out'): s.execute(c, 'trial1')
            self.assertEqual(sum('--force' in call.args[0] for call in run.call_args_list), 1)
            report = json.loads(proof.with_name('syslog-experiment.json').read_text())
            self.assertEqual(report['failed_step'], 'graceful-stop-1')
            self.assertEqual(live['status'], 'Running')

    def test_plan_does_not_execute(self):
        argv = ['storage_syslog_live.py', '--config', '/fixture.json', '--trial', 'trial1']
        with patch.object(sys, 'argv', argv), patch.object(s.p, 'config', return_value={}), \
             patch.object(s.p, 'check_host'), patch.object(s, 'execute') as execute, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            s.main(); execute.assert_not_called()
            self.assertFalse(json.loads(out.getvalue())['apply'])


if __name__ == '__main__': unittest.main()

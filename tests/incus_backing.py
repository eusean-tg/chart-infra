#!/usr/bin/env python3
"""Offline backing-service guard and deployment-contract checks."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'incus'))
import backing as b


class Backing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'identity/backing'
        self.state.mkdir(parents=True)
        self.data = self.root / 'datasets/fixture-a'
        (self.data / 'mongo/member-0').mkdir(parents=True)
        (self.data / 'dragonfly').mkdir()
        (self.data / '.chart-dataset').write_text('unique')
        (self.data / 'mongo/member-0/WiredTiger').write_text('fixture')
        self.marker = {'owner': 'chart-incus-v1', 'name': 'test-box'}
        self.inv = {'owner': b.OWNER, 'box': 'test-box', 'box_marker': self.marker,
                    'dataset': 'fixture-a', 'dataset_marker': 'unique', 'tailscale_ip': '100.100.1.1',
                    'images': b.IMAGES, 'phase': 'ready', 'identity_hashes': {}}
        self.patches = [patch.object(b, 'ROOT', self.root), patch.object(b, 'STATE', self.state)]
        for p in self.patches:
            p.start()
        self.save()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def save(self):
        (self.state / 'inventory.json').write_text(json.dumps(self.inv))
        (self.state / 'compose.json').write_text(json.dumps(b.compose_spec(self.inv)))

    def test_initialized_data_loss_refused(self):
        (self.data / 'mongo/member-0/WiredTiger').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Initialized Mongo data missing'):
            b.inventory('test-box', self.marker)

    def test_replaced_marker_refused(self):
        (self.data / '.chart-dataset').write_text('foreign')
        with self.assertRaisesRegex(RuntimeError, 'Dataset identity'):
            b.inventory('test-box', self.marker)

    def test_symlinked_dataset_refused(self):
        (self.data / 'mongo/member-0/WiredTiger').unlink()
        (self.data / 'mongo/member-0/WiredTiger').symlink_to(self.data / '.chart-dataset')
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            b.inventory('test-box', self.marker)

    def test_identity_mutation_refused(self):
        key = self.state / 'keyfile'
        key.write_text('key')
        self.inv['identity_hashes']['keyfile'] = b.digest(key)
        self.save()
        key.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'identity/config differs'):
            b.inventory('test-box', self.marker)

    def test_changed_compose_refused(self):
        spec = b.compose_spec(self.inv)
        spec['services']['mongo']['ports'] = ['0.0.0.0:27017:27017']
        (self.state / 'compose.json').write_text(json.dumps(spec))
        with self.assertRaisesRegex(RuntimeError, 'Compose contract differs'):
            b.inventory('test-box', self.marker)

    def test_empty_dataset_only_explicit_initialization(self):
        (self.data / 'mongo/member-0/WiredTiger').unlink()
        self.inv['phase'] = 'prepared'
        self.save()
        b.inventory('test-box', self.marker)

    def test_retained_mounts_offline_and_on_demand(self):
        spec = b.compose_spec(self.inv)
        self.assertTrue(spec['networks']['runtime']['internal'])
        self.assertNotIn('ports', spec['services']['redis'])
        for s in spec['services'].values():
            self.assertEqual(s['restart'], 'no')
            self.assertEqual(s['pull_policy'], 'never')
            self.assertTrue(s['read_only'])
            for v in s['volumes']:
                self.assertFalse(v['bind']['create_host_path'])
                self.assertTrue(v['source'].startswith(str(self.root)))
            for field in ('deploy', 'mem_limit', 'cpus', 'cpu_shares', 'cpu_quota'):
                self.assertNotIn(field, s)

    def test_invalid_selection_never_creates_paths(self):
        with self.assertRaises(RuntimeError):
            b.prepare('test-box', self.marker, '../foreign', '100.100.1.1')
        self.assertFalse((self.root / 'foreign').exists())

    def test_docker_uses_box_local_socket(self):
        with patch.object(b.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            b.run(['docker', 'ps'])
            self.assertEqual(run.call_args.args[0], ['docker', '--host=unix:///var/run/docker.sock', 'ps'])

    def test_credentials_redacted_from_failures(self):
        c = {'redis_password': 'cache-secret', 'mongo': {'admin': {'password': 'admin-secret'}}}
        (self.state / 'credentials.json').write_text(json.dumps(c))
        with patch.object(b.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'admin-secret cache-secret')):
            with self.assertRaises(RuntimeError) as error:
                b.run(['docker', 'exec'])
            self.assertNotIn('admin-secret', str(error.exception))
            self.assertNotIn('cache-secret', str(error.exception))

    def test_attached_other_clients_block_lifecycle(self):
        network = {'Internal': True, 'Labels': {b.LABEL: b.OWNER, 'chart-infra.box': 'test-box'},
                   'Containers': {'foreign-client': {}}}
        with patch.object(b, 'run', side_effect=['', 'net', json.dumps([network])]):
            with self.assertRaisesRegex(RuntimeError, 'Other runtime clients'):
                b.ownership('test-box')


if __name__ == '__main__':
    unittest.main()

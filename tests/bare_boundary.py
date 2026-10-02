#!/usr/bin/env python3
"""Offline artifact inspection and final-test cleanup guards."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import artifact_check as a
spec = importlib.util.spec_from_file_location('boundary', Path(__file__).with_name('bare_boundary_live.py'))
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, extra=None, machine=b''):
        path = self.root / 'image.tar'
        entries = {'rootfs/etc/machine-id': machine, 'rootfs/var/lib/chart-bare-image': b'chart-bare-v1\n'}
        entries.update(extra or {})
        with tarfile.open(path, 'w') as tar:
            directory = tarfile.TarInfo('rootfs/srv/chart/source'); directory.type = tarfile.DIRTYPE
            tar.addfile(directory)
            for name, content in entries.items():
                entry = tarfile.TarInfo(name); entry.size = len(content); tar.addfile(entry, io.BytesIO(content))
        return path

    def test_inspects_without_extracting(self):
        path = self.archive()
        result = a.inspect(path, a.p.digest(path))
        self.assertFalse(result['extracted']); self.assertEqual(list(self.root.iterdir()), [path])

    def test_rejects_nonempty_machine_id(self):
        path = self.archive(machine=b'copied-id')
        with self.assertRaisesRegex(RuntimeError, 'marker content'): a.inspect(path, a.p.digest(path))

    def test_rejects_identity_source_and_provisioning_leftovers(self):
        for name in ('rootfs/etc/ssh/ssh_host_ed25519_key', 'rootfs/root/.ssh/authorized_keys',
                     'rootfs/var/lib/tailscale/tailscaled.state', 'rootfs/root/chart-prep/input',
                     'rootfs/srv/chart/source/repo/src.ts', 'rootfs/root/.npmrc'):
            with self.subTest(name=name):
                path = self.archive({name: b'fixture'})
                with self.assertRaises(RuntimeError): a.inspect(path, a.p.digest(path))

    def test_rejects_checksum_substitution(self):
        path = self.archive()
        with self.assertRaisesRegex(RuntimeError, 'checksum'): a.inspect(path, '0' * 64)

    def test_rejects_unsafe_paths(self):
        path = self.archive({'rootfs/../escape': b'fixture'})
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'): a.inspect(path, a.p.digest(path))

    def test_positive_control_cannot_pass_on_connection_failure(self):
        with patch.object(b, 'probe', return_value={'connected': False}):
            with self.assertRaisesRegex(RuntimeError, 'Positive'): b.assert_probe({}, None, '127.0.0.1', 1, 'token', True)

    def test_negative_probe_rejects_any_successful_connection(self):
        with patch.object(b, 'probe', return_value={'connected': True, 'value': 'other-service'}):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected'): b.assert_probe({}, None, '127.0.0.1', 1, 'token', False)

    def test_retirement_refuses_changed_identity_before_any_mutation(self):
        with patch.object(b, 'endpoint', return_value={'name': 'chart-test-a', 'node_id': 'changed'}), \
             patch.object(b.h, 'save') as save, patch.object(b.backup, 'backup') as copy:
            with self.assertRaisesRegex(RuntimeError, 'changed before retirement'):
                b.retire({}, [{'name': 'chart-test-a', 'node_id': 'original'}], self.root, {})
            save.assert_not_called(); copy.assert_not_called()

    def test_retirement_preserves_copies_before_logout_and_detaches_before_delete(self):
        target = {'name': 'chart-test-a', 'node_id': 'original'}; events = []
        def guest(c, name, args): events.append(args[0] + ' ' + args[1])
        def command(args): events.append(args[1])
        def copy(*args, **kwargs):
            self.assertTrue(kwargs['rootfs']); events.append('backup'); return self.root / 'retained-copy'
        with patch.object(b, 'endpoint', return_value=target), patch.object(b.backup, 'backup', side_effect=copy), \
             patch.object(b.h, 'wait_ready'), patch.object(b.h, 'guest', side_effect=guest), \
             patch.object(b.h, 'instance', return_value={'status': 'Stopped'}), patch.object(b.p, 'run', side_effect=command):
            record = {}; b.retire({'project': 'p', 'boxes_root': str(self.root / 'hdd')}, [target], self.root, record)
        self.assertEqual(events, ['backup', 'tailscale logout', 'stop', 'config', 'delete'])
        self.assertEqual(record['retirement']['chart-test-a']['tailnet_removal'], 'pending authorized console/API removal')

    def test_fixture_server_builds_without_downloads(self):
        source = self.root / 'server.c'; source.write_text(b.SERVER.replace('TOKEN', 'synthetic-token'))
        subprocess.run(['gcc', '-static', str(source), '-o', str(self.root / 'server')], check=True, capture_output=True)


if __name__ == '__main__': unittest.main()

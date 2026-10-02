#!/usr/bin/env python3
"""Offline source, dependency and app-isolation contract checks."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import box
import backing as b


class Apps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.patches = [patch.object(box, 'STATE', self.state), patch.object(box, 'SOURCE', self.source),
                        patch.object(b, 'save', side_effect=lambda p, v: p.write_text(json.dumps(v)))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def archive(self, names, symlink=False):
        archive = self.root / 'source.tar'
        with tarfile.open(archive, 'w') as f:
            for name, text in names.items():
                t = tarfile.TarInfo(name)
                t.size = len(text)
                if symlink:
                    t.type = tarfile.SYMTYPE
                    t.linkname = '/etc/passwd'
                    t.size = 0
                f.addfile(t, io.BytesIO(text.encode()))
        return archive

    def test_archive_blocks_traversal_before_writes(self):
        a = self.archive({'../bad': 'bad'})
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
            box.stage('baseline', 'auth', str(a), b.digest(a), 'a' * 40)
        self.assertFalse((self.source / 'baseline').exists())

    def test_archive_blocks_included_symlinks(self):
        a = self.archive({'src/key': ''}, symlink=True)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            box.stage('baseline', 'auth', str(a), b.digest(a), 'a' * 40)

    def test_source_filters_secrets_and_detects_changes(self):
        a = self.archive({'package.json': '{}', 'pnpm-lock.yaml': 'lockfileVersion: 9',
                          '.env.local': 'SECRET=value', '.npmrc': 'token=value', '.git/config': 'private',
                          'keys/signing.pem': 'private', '.env.sample': 'EXAMPLE=fixture', 'src/index.ts': 'export {}'})
        box.stage('baseline', 'auth', str(a), b.digest(a), 'a' * 40)
        p = self.source / 'baseline/auth-service-backend'
        for name in ('.env.local', '.npmrc', '.git', 'keys/signing.pem'):
            self.assertFalse((p / name).exists())
        self.assertTrue((p / '.env.sample').exists())
        box.source_record('baseline', 'auth')
        (p / 'src/index.ts').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            box.source_record('baseline', 'auth')

    def test_dependency_key_changes_with_lock_and_runtime(self):
        (self.root / 'pnpm-lock.yaml').write_text('a')
        first = box.key(self.root, 'image1')
        self.assertNotEqual(first, box.key(self.root, 'image2'))
        (self.root / 'pnpm-lock.yaml').write_text('b')
        self.assertNotEqual(first, box.key(self.root, 'image1'))

    def test_runtime_mounts_and_network_are_restricted(self):
        spec = box.spec({'box': 'test-box', 'workspace': 'baseline', 'image': 'sha256:fixture',
                         'deps': {s: {'volume': 'deps-' + s} for s in box.SERVICES}})
        self.assertEqual(spec['networks'], {'runtime': {'external': True, 'name': 'chart-backing-runtime'}})
        for service in spec['services'].values():
            self.assertEqual(service['user'], '1000:1000')
            self.assertEqual(service['restart'], 'no')
            self.assertEqual(service['pull_policy'], 'never')
            self.assertNotIn('ports', service)
            self.assertEqual(service['networks'], ['runtime'])
            for m in service['volumes']:
                self.assertTrue(m['read_only'])
                self.assertNotIn('npmrc', m['source'])
                if m['type'] == 'bind': self.assertFalse(m['bind']['create_host_path'])
            for field in ('deploy', 'mem_limit', 'cpus', 'cpu_quota', 'cpu_shares'):
                self.assertNotIn(field, service)
        self.assertTrue(all(v['external'] for v in spec['volumes'].values()))

    def test_selection_requires_stopped_writers(self):
        with patch.object(box, 'containers', return_value=[{'State': {'Running': True}}]):
            with self.assertRaisesRegex(RuntimeError, 'Stop app writers'):
                box.select('test-box', 'baseline')

    def test_foreign_dependency_volume_refused(self):
        obj = {'Driver': 'local', 'Options': {}, 'Labels': {'foreign': 'true'}}
        with patch.object(b, 'run', return_value=json.dumps([obj])):
            with self.assertRaisesRegex(RuntimeError, 'Foreign'):
                box.volume('volume', {b.LABEL: box.OWNER})

    def test_existing_identity_is_verified_not_regenerated(self):
        (self.state / 'identity.json').write_text('{}')
        with patch.object(box.box_config, 'verify', side_effect=RuntimeError('missing retained key')), \
             patch.object(b, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'missing retained key'):
                box.box_config.prepare(self.state)
            run.assert_not_called()


if __name__ == '__main__': unittest.main()

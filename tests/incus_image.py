#!/usr/bin/env python3
"""Offline image-input, sanitization, adoption and promotion guard tests."""
import io
import contextlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import image
import image_guest as seed
import box
import backing as b


class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, entries):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as tar:
            for name, value in entries.items():
                t = tarfile.TarInfo(name); t.size = len(value)
                tar.addfile(t, io.BytesIO(value))
        return data.getvalue()

    def test_image_filter_preserves_templates_and_removes_private_files(self):
        data = self.archive({'src/index.ts': b'export {}', '.env.sample': b'private-key-example',
                             '.env.local': b'secret', '.npmrc': b'token', '.git/config': b'origin',
                             'node_modules/native': b'wrong platform'})
        path = self.root / 'source.tar'
        image.filter_archive(data, path)
        with tarfile.open(path) as tar:
            self.assertEqual(tar.getnames(), ['src/index.ts', '.env.sample'])
        self.assertFalse(box.source_policy.ignored(('.env.sample',)))

    def test_traversal_and_case_collisions_rejected(self):
        for i, data in enumerate((self.archive({'../escape': b'bad'}), self.archive({'src/A': b'a', 'src/a': b'b'}))):
            with self.assertRaises(RuntimeError):
                image.filter_archive(data, self.root / (str(i) + '.tar'))

    def test_build_names_cannot_address_existing_profile(self):
        with self.assertRaises(RuntimeError): image.names('../sean-dev-pilot')
        builder, accepts = image.names('test-seed')
        self.assertEqual(builder, 'chart-bld-test-seed')
        self.assertEqual(accepts, ['chart-accept-test-seed-a', 'chart-accept-test-seed-b'])

    def test_pins_require_exact_backend_set_and_commit(self):
        pins = json.loads((image.p.HERE / 'sources.lock.json').read_text())
        path = self.root / 'pins.json'
        path.write_text(json.dumps(pins))
        self.assertEqual(image.pins(path), pins)
        pins['repos']['orange-v2-backend']['sha'] = 'main'
        path.write_text(json.dumps(pins))
        with self.assertRaises(RuntimeError): image.pins(path)

    def test_known_token_scanner_checks_chunk_boundaries_and_node_modules(self):
        path = self.root / 'node_modules'; path.mkdir()
        file = path / 'test'
        file.write_bytes(b'x' * (1024 * 1024 - 5) + b'fixture-secret-token')
        with self.assertRaisesRegex(RuntimeError, 'Credential material'):
            seed.scan_known_credentials([self.root], [b'fixture-secret-token'])

    def test_source_private_key_rejected_but_header_placeholder_allowed(self):
        path = self.root / 'source'; path.mkdir()
        (path / 'example.ts').write_text('-----BEGIN PRIVATE KEY-----\\nPLACEHOLDER')
        seed.source_credentials(path)
        (path / 'example.ts').write_text('-----BEGIN PRIVATE KEY-----\\n' + 'A' * 64)
        with self.assertRaisesRegex(RuntimeError, 'Credential-like'):
            seed.source_credentials(path)

    def test_versioned_sample_kept_while_known_installer_token_is_rejected(self):
        (self.root / '.env.sample').write_text('KEY="-----BEGIN PRIVATE KEY-----\\n' + 'A' * 64 + '"')
        seed.source_credentials(self.root)
        with (self.root / '.env.sample').open('a') as f:
            f.write('\nTOKEN=actual-installer-token')
        with self.assertRaisesRegex(RuntimeError, 'Credential material'):
            seed.scan_known_credentials([self.root], [b'actual-installer-token'])

    def test_multi_registry_config_projects_only_npmjs(self):
        path = self.root / 'npmrc'
        path.write_text('//other.example/:_authToken=unrelated\n//registry.npmjs.org/:_authToken=selected\n')
        self.assertEqual(image.registry_line(path), '//registry.npmjs.org/:_authToken=selected\n')
        path.write_text('//registry.npmjs.org/:_authToken=${TOKEN}\n')
        with self.assertRaises(RuntimeError): image.registry_line(path)

    def test_failed_acceptance_never_promotes_or_changes_record(self):
        directory = self.root / 'images/test'; directory.mkdir(parents=True)
        record = {'owner': image.OWNER, 'phase': 'acceptance-pending', 'fingerprint': 'candidate',
                  'acceptance_boxes': ['chart-accept-test-a', 'chart-accept-test-b'],
                  'promote_after_acceptance': True}
        path = directory / 'build.json'; path.write_text(json.dumps(record))
        def guest(_config, _box, argv):
            if argv[:2] == ['tailscale', 'ip']: return '100.100.100.1'
            raise RuntimeError('fixture failed')
        with patch.object(image.p, 'STATE', self.root), patch.object(image.p, 'config', return_value={'project': 'test'}), \
             patch.object(image.p, 'check_host'), patch.object(image.p, 'locked', contextlib.nullcontext), \
             patch.object(image.p, 'query', return_value={'public': False, 'properties': {'chart.build': 'test'}}), \
             patch.object(image, 'check_owned'), patch.object(image, 'push'), patch.object(image, 'guest', side_effect=guest), \
             patch.object(image, 'alias_update') as promote:
            with self.assertRaisesRegex(RuntimeError, 'fixture failed'):
                image.verify(SimpleNamespace(config='fixture', build='test', apply=True))
            promote.assert_not_called()
        self.assertEqual(json.loads(path.read_text()), record)

    def test_foreign_alias_not_replaced(self):
        with patch.object(image.p, 'query', return_value=[{'name': 'chart-golden', 'description': 'other', 'target': 'old'}]) as q:
            with self.assertRaisesRegex(RuntimeError, 'Foreign alias'):
                image.alias_update({'project': 'test'}, 'chart-golden', 'new')
            q.assert_called_once()

    def test_owned_alias_updates_target_without_deleting_image(self):
        with patch.object(image.p, 'query', side_effect=[[{'name': 'chart-golden', 'description': image.OWNER, 'target': 'old'}], None]) as q:
            image.alias_update({'project': 'test'}, 'chart-golden', 'new')
            self.assertEqual(q.call_args.args[2], 'PUT')
            self.assertEqual(q.call_args.args[1]['target'], 'new')

    def test_adoption_refuses_existing_profile_state(self):
        state = self.root / 'apps'; state.mkdir()
        seedroot = self.root / 'seed'; seedroot.mkdir()
        (seedroot / 'manifest.json').write_text('{}')
        (seedroot / 'sealed').write_text(b.digest(seedroot / 'manifest.json'))
        with patch.object(box, 'STATE', state), patch.object(seed, 'SEED', seedroot), \
             patch.object(b, 'guard', return_value={}), patch.object(b, 'inventory'), \
             patch.object(b, 'ownership'), patch.object(box, 'containers', return_value=[]), \
             patch.object(seed, 'validate_manifest', return_value={'build': 'test'}):
            with self.assertRaisesRegex(RuntimeError, 'retained-data adoption'):
                seed.adopt('test-box')
        self.assertEqual(list(state.iterdir()), [])

    def test_bad_artifact_checksum_refused(self):
        inputs = {'schema': 1, 'pins': {'repos': {}}, 'sources': {}, 'runtime': {'image': 'sha256:' + 'a' * 64,
                  'archive': 'runtime.tar', 'sha256': 'b' * 64}}
        for repo in box.source_policy.REPOS.values():
            inputs['pins']['repos'][repo] = {'sha': 'c' * 40}
            inputs['sources'][repo] = {'revision': 'c' * 40, 'archive': repo + '.tar', 'sha256': 'd' * 64}
            (self.root / (repo + '.tar')).write_text('different')
        (self.root / 'inputs.json').write_text(json.dumps(inputs))
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            image.inputs(self.root)

    def test_builder_guard_refuses_hdd_identity(self):
        marker = self.root / 'builder'
        marker.write_text(json.dumps({'owner': image.OWNER, 'name': 'fixture'}))
        (self.root / 'identity').mkdir()
        with patch.object(seed, 'BUILD_MARKER', marker), patch.object(b, 'ROOT', self.root), \
             patch.object(seed.os, 'geteuid', return_value=0), patch.object(seed.socket, 'gethostname', return_value='fixture'):
            with self.assertRaisesRegex(RuntimeError, 'identity forbidden'):
                seed.builder()


if __name__ == '__main__': unittest.main()

#!/usr/bin/env python3
"""Offline checks for retained handover, ownership, freeze and mixed source activation."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import backing as b
import box
import box_sync as sync
import sync_policy as policy
import laptop_sync as laptop


class Handover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        (self.state / 'sources').mkdir(parents=True)
        self.source = self.root / 'source'
        self.records = {}
        for service, (repo, _, _) in box.SERVICES.items():
            path = self.source / 'baseline' / repo
            (path / 'src').mkdir(parents=True)
            for name, value in [('package.json', '{}'), ('pnpm-lock.yaml', 'lock'), ('src/index.ts', 'original')]:
                (path / name).write_text(value)
            (path / 'node_modules').mkdir()
            self.records[service] = {'kind': 'bundle', 'path': str(path), 'revision': 'a' * 40,
                                    'fingerprint': policy.fingerprint(policy.manifest(path))}
        (self.state / 'sources/baseline.json').write_text(json.dumps(self.records))
        for item in (patch.object(box, 'STATE', self.state), patch.object(box, 'SOURCE', self.source),
                     patch.object(box, 'containers', return_value=[]), patch.object(box, 'capacity'),
                     patch.object(b, 'save', side_effect=lambda p, v: p.write_text(json.dumps(v)))):
            item.start()
            self.addCleanup(item.stop)

    def prepared(self):
        exp = sync.prepare('baseline', 'auth')
        p = Path(exp['path'])
        payload = {'registration': exp['registration'], 'policy_hash': policy.policy_hash(),
                   'client_id': 'b' * 48, 'session': 'sync_fixture123', 'path': '/laptop/auth'}
        sync.register('baseline', 'auth', payload)
        for name, value in [('package.json', '{}'), ('pnpm-lock.yaml', 'lock'), ('src/index.ts', 'laptop')]:
            (p / name).parent.mkdir(parents=True, exist_ok=True)
            (p / name).write_text(value)
        payload.update(paused=True, fingerprint=policy.fingerprint(policy.manifest(p)), head='c' * 40, dirty=False)
        sync.checkpoint('baseline', 'auth', payload)
        return exp, p, payload

    def test_policy_separate_from_k3s(self):
        self.assertNotEqual(policy.policy_hash(), policy.common.policy_hash())
        self.assertEqual(policy.common.configuration()['permissions']['defaultFileMode'], '0600')
        self.assertEqual(policy.configuration()['permissions']['defaultFileMode'], '0644')

    def test_copied_helpers_prefer_their_sibling_common_module(self):
        tools = self.root / 'tools'
        tools.mkdir()
        shutil.copy2(policy.__file__, tools / 'sync_policy.py')
        shutil.copy2(policy.common.__file__, tools / 'sync_common.py')
        (self.root / 'sync_common.py').write_text('raise RuntimeError("wrong parent helper")')
        result = subprocess.check_output([sys.executable, '-c',
            'import sync_policy; print(sync_policy.policy_hash())'], cwd=tools, text=True)
        self.assertEqual(result.strip(), policy.policy_hash())

    def test_handover_retains_original_and_other_bundles(self):
        original = Path(self.records['auth']['path'])
        inode = sync.tree_id(original)
        exp = sync.prepare('baseline', 'auth')
        r = sync.read('baseline', 'auth')
        self.assertEqual(sync.tree_id(Path(r['retained'])), inode)
        self.assertEqual((Path(r['retained']) / 'src/index.ts').read_text(), 'original')
        self.assertEqual(policy.manifest(original), {})
        self.assertNotEqual(sync.tree_id(original), inode)
        self.assertEqual(sync.prepare('baseline', 'auth'), exp)
        self.assertEqual(box.source_record('baseline', 'orange'), self.records['orange'])
        with self.assertRaisesRegex(RuntimeError, 'checkpoint'):
            box.source_record('baseline', 'auth')

    def test_modified_seed_is_not_moved(self):
        path = Path(self.records['auth']['path'])
        (path / 'src/index.ts').write_text('retain this edit')
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            sync.prepare('baseline', 'auth')
        self.assertEqual((path / 'src/index.ts').read_text(), 'retain this edit')
        self.assertFalse(sync.state_path('baseline', 'auth').exists())

    def test_running_apps_block_handover(self):
        with patch.object(box, 'containers', return_value=[{'State': {'Running': True}}]):
            with self.assertRaisesRegex(RuntimeError, 'Stop app'):
                sync.prepare('baseline', 'auth')

    def test_interrupted_after_rename_recovers_without_deletion(self):
        save = sync.save
        def fail(w, service, r):
            if r['phase'] == 'ready':
                raise RuntimeError('interruption')
            save(w, service, r)
        with patch.object(sync, 'save', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                sync.prepare('baseline', 'auth')
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            box.source_record('baseline', 'auth')
        sync.prepare('baseline', 'auth')
        self.assertEqual(sync.read('baseline', 'auth')['phase'], 'ready')

    def test_replaced_root_and_wrong_owner_refused(self):
        exp, path, payload = self.prepared()
        wrong = {**payload, 'client_id': 'd' * 48}
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            sync.invalidate('baseline', 'auth', wrong)
        path.rename(path.with_name('retained-mirror'))
        path.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'directory replaced'):
            box.source_record('baseline', 'auth')

    def test_freeze_rejects_later_edits_and_extra_remote_files(self):
        exp, path, payload = self.prepared()
        box.source_record('baseline', 'auth', frozen=True)
        (path / 'extra.ts').write_text('box edit')
        with self.assertRaisesRegex(RuntimeError, 'after freeze'):
            box.source_record('baseline', 'auth', frozen=True)
        with self.assertRaisesRegex(RuntimeError, 'fingerprints differ'):
            sync.checkpoint('baseline', 'auth', payload)

    def test_resume_invalidates_freeze_but_keeps_hot_reload(self):
        exp, path, payload = self.prepared()
        sync.invalidate('baseline', 'auth', payload)
        (path / 'src/index.ts').write_text('next laptop save')
        box.source_record('baseline', 'auth')
        with self.assertRaisesRegex(RuntimeError, 'Freeze'):
            box.source_record('baseline', 'auth', frozen=True)

    def test_matching_existing_dependency_is_reused(self):
        exp, path, payload = self.prepared()
        image = 'sha256:' + 'a' * 64
        key = box.key(path, image)
        depdir = self.state / 'dependencies/baseline'
        depdir.mkdir(parents=True)
        dep = {'key': key, 'image': image, 'volume': 'old-volume', 'labels': {'fixture': 'true'}}
        (depdir / 'auth.json').write_text(json.dumps(dep))
        modules = self.root / 'modules'
        modules.mkdir()
        (modules / '.chart-installed').write_text(key)
        with patch.object(box, 'runtime', return_value={'image': image}), \
             patch.object(box, 'volume', return_value={'Mountpoint': str(modules)}), \
             patch.object(box, 'npm_token') as token:
            box.install_deps('baseline', 'auth', 'test')
            token.assert_not_called()
            (path / 'package.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(RuntimeError, 'Dependency inputs changed'):
                box.dependency('baseline', 'auth')

    def test_activation_generation_changes_compose_only_for_mirror(self):
        exp, path, payload = self.prepared()
        inv = {'box': 'test-box', 'workspace': 'baseline', 'image': 'fixture',
               'deps': {s: {'volume': s} for s in box.SERVICES}}
        before = box.spec(inv)
        inv['sources'] = {'auth': exp['registration']}
        after = box.spec(inv)
        self.assertNotEqual(before['services']['auth'], after['services']['auth'])
        self.assertEqual(before['services']['orange'], after['services']['orange'])
        self.assertEqual(after['services']['auth']['labels']['chart-infra.source'], exp['registration'])

    def test_resume_running_unselected_mirror_refused(self):
        exp, path, payload = self.prepared()
        with patch.object(box, 'containers', return_value=[{'State': {'Running': True}}]), \
             patch.object(box, 'selection', return_value={'workspace': 'baseline'}):
            with self.assertRaisesRegex(RuntimeError, 'not selected'):
                sync.invalidate('baseline', 'auth', payload)
        self.assertTrue(sync.read('baseline', 'auth')['checkpoint']['paused'])

    def test_plain_pause_is_not_freeze(self):
        r = {'paused': True, 'alpha': {}, 'beta': {}}
        self.assertTrue(laptop.clean(r, paused=True))
        r['beta']['scanProblems'] = ['problem']
        self.assertFalse(laptop.clean(r, paused=True))


if __name__ == '__main__':
    unittest.main()

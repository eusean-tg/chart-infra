#!/usr/bin/env python3
"""Offline identity and pool-boundary checks for storage moves."""
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
import storage_move as m


class StorageMove(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((ROOT / 'incus/host.example.json').read_text())
        self.c.update(machine_id='a'*32, hdd_uuid='14ef1cfa-28ee-4986-8989-2e248896bf07')
        self.before = m.p.instance_spec(self.c, 'developer')
        self.before['config'].update({'volatile.idmap.current': 'map', 'volatile.eth0.hwaddr': 'mac',
                                      'volatile.uuid': 'identity'})
        self.before['status'] = 'Stopped'

    def test_move_only_changes_root_pool(self):
        after = copy.deepcopy(self.before); after['devices']['root']['pool'] = 'hdd'
        m.validate_move(self.before, after, 'hdd')
        for kind in ('mount', 'idmap', 'mac', 'profile', 'security', 'name'):
            changed = copy.deepcopy(after)
            if kind == 'mount': changed['devices']['data']['source'] = '/wrong'
            if kind == 'idmap': changed['config']['volatile.idmap.current'] = 'different'
            if kind == 'mac': changed['config']['volatile.eth0.hwaddr'] = 'different'
            if kind == 'profile': changed['profiles'] = ['default']
            if kind == 'security': changed['config']['security.privileged'] = 'true'
            if kind == 'name': changed['name'] = 'other'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                m.validate_move(self.before, changed, 'hdd')

    def test_running_instance_cannot_move(self):
        after = copy.deepcopy(self.before); after['status'] = 'Running'
        with patch.object(m.h, 'instance', return_value=after), patch.object(m.p, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'stopped'): m.transfer(self.c, 'developer', self.before, 'hdd')
            run.assert_not_called()

    def mapped(self):
        obj = copy.deepcopy(self.before)
        mapping = json.dumps([{'Isuid': uid, 'Isgid': not uid, 'Hostid': 1393216,
                               'Nsid': 0, 'Maprange': 65536} for uid in (True, False)])
        obj['config'].update({'volatile.idmap.base': '1393216', 'volatile.idmap.current': mapping,
                              'volatile.idmap.next': mapping, 'volatile.last_state.idmap': '[]',
                              'volatile.cloud-init.instance-id': 'original-cloud-id'})
        return obj

    def test_boot_metadata_is_identity_checked(self):
        before = self.mapped()
        for key in ('volatile.cloud-init.instance-id', 'volatile.apply_template',
                    'volatile.last_state.idmap', 'volatile.unrecognized'):
            after = copy.deepcopy(before); after['config'][key] = 'changed'
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                m.validate_move(before, after, 'ssd')

    def test_only_ephemeral_network_and_power_fields_can_differ(self):
        after = copy.deepcopy(self.before)
        for key in m.RUNTIME_KEYS: after['config'][key] = 'regenerated'
        m.validate_move(self.before, after, 'ssd')

    def test_range_check_covers_other_projects_and_pending_maps(self):
        before = self.mapped()
        own = {**before, 'project': self.c['project']}
        other = {**before, 'project': 'foreign', 'expanded_config': before['config']}
        with patch.object(m.p, 'query', return_value=[own, other]):
            with self.assertRaisesRegex(RuntimeError, 'overlaps'): m.original_range_free(self.c, before)
        other['expanded_config'] = {'volatile.idmap.next': before['config']['volatile.idmap.next']}
        with patch.object(m.p, 'query', return_value=[own, other]):
            with self.assertRaisesRegex(RuntimeError, 'overlaps'): m.original_range_free(self.c, before)
        other['expanded_config'] = {'volatile.idmap.next': '[]'}
        with patch.object(m.p, 'query', return_value=[own, other]) as query:
            m.original_range_free(self.c, before)
            self.assertIn('all-projects=true', query.call_args.args[0])

    def test_invalid_original_map_refuses_before_inventory(self):
        for change in ({'raw.idmap': 'both 0 0'}, {'volatile.idmap.current': '[]'},
                       {'security.idmap.isolated': 'false'}, {'security.idmap.size': '1'}):
            before = self.mapped(); before['config'].update(change)
            with self.subTest(change=change), patch.object(m.p, 'query') as query:
                with self.assertRaises(RuntimeError): m.original_range_free(self.c, before)
                query.assert_not_called()

    def test_repair_is_narrow_and_keeps_evidence(self):
        before = self.mapped(); after = copy.deepcopy(before)
        after['devices']['root']['pool'] = 'hdd'
        repaired = copy.deepcopy(after)
        after['config'].update({'volatile.idmap.base': '1458752',
                                'volatile.idmap.next': before['config']['volatile.idmap.next'].replace('1393216', '1458752'),
                                'volatile.cloud-init.instance-id': 'regenerated', 'volatile.apply_template': 'copy'})
        with tempfile.TemporaryDirectory() as d, patch.object(m.h, 'instance', side_effect=[after, repaired]), \
             patch.object(m, 'original_range_free') as free, patch.object(m.p, 'query') as query:
            receipt = Path(d)/'repair.json'
            m.restore_move_metadata(self.c, 'developer', before, 'hdd', receipt)
            free.assert_called_once_with(self.c, before)
            args = query.call_args.args
            self.assertEqual(args[2], 'PATCH')
            self.assertEqual(set(args[1]['config']), set(m.RESET_KEYS))
            self.assertEqual(args[1]['config']['volatile.apply_template'], '')
            evidence = json.loads(receipt.read_text())
            self.assertEqual(evidence['phase'], 'verified')
            self.assertEqual(evidence['before_repair'], after)

    def test_repair_rejects_unexpected_state_without_writing(self):
        for kind in ('running', 'current-map', 'mount', 'source-template', 'new-template', 'uuid'):
            before = self.mapped(); after = copy.deepcopy(before)
            after['devices']['root']['pool'] = 'hdd'
            if kind == 'running': after['status'] = 'Running'
            if kind == 'current-map': after['config']['volatile.idmap.current'] = '[]'
            if kind == 'mount': after['devices']['data']['source'] = '/wrong'
            if kind == 'source-template': before['config']['volatile.apply_template'] = 'create'
            if kind == 'new-template': after['config']['volatile.apply_template'] = 'create'
            if kind == 'uuid': after['config']['volatile.uuid'] = 'wrong'
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d, \
                 patch.object(m.h, 'instance', return_value=after), patch.object(m.p, 'query') as query:
                receipt = Path(d)/'repair.json'
                with self.assertRaises(RuntimeError):
                    m.restore_move_metadata(self.c, 'developer', before, 'hdd', receipt)
                query.assert_not_called(); self.assertFalse(receipt.exists())

    def test_failed_patch_retains_prepared_receipt(self):
        before = self.mapped(); after = copy.deepcopy(before)
        after['devices']['root']['pool'] = 'hdd'; after['config']['volatile.apply_template'] = 'copy'
        with tempfile.TemporaryDirectory() as d, patch.object(m.h, 'instance', return_value=after), \
             patch.object(m, 'original_range_free'), patch.object(m.p, 'query', side_effect=RuntimeError('API failed')):
            receipt = Path(d)/'repair.json'
            with self.assertRaisesRegex(RuntimeError, 'API failed'):
                m.restore_move_metadata(self.c, 'developer', before, 'hdd', receipt)
            self.assertEqual(json.loads(receipt.read_text())['phase'], 'prepared')

    def test_resume_preserves_original_failure_and_reuses_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            c = {**self.c, 'hdd_mount': d}; trial = 'trial1'; name = 'chart-move-' + trial
            directory = Path(d)/'state/storage-moves'/trial; directory.mkdir(parents=True)
            data = Path(d)/'shared-dev/storage-fixtures'/trial; data.mkdir(parents=True)
            (data/'.chart-storage-fixture.json').write_text(json.dumps(
                {'owner': m.OWNER, 'trial': trial, 'host': c['machine_id']}))
            before = self.mapped(); before['name'] = name
            record = {'owner': m.OWNER, 'host': c['machine_id'], 'trial': trial, 'fixture': name,
                      'data': str(data), 'image': 'f'*64, 'phase': 'failed', 'failed_step': 'moving-to-hdd',
                      'moves': [], 'instance_before': before, 'baseline': {}}
            original = directory/'verify.json'; original.write_text(json.dumps(record)); saved = original.read_bytes()
            obj = copy.deepcopy(before); obj['devices']['root']['pool'] = 'hdd'
            def transfer(c, name, before, pool, evidence): obj['devices']['root']['pool'] = pool
            with patch.object(m.p, 'STATE', Path(d)/'state'), patch.object(m.h, 'host'), \
                 patch.object(m.h, 'candidate'), patch.object(m, 'hdd_capacity'), \
                 patch.object(m.t, 'validate_fixture', return_value=obj), patch.object(m.h, 'instance', return_value=obj), \
                 patch.object(m, 'restore_move_metadata') as repair, patch.object(m, 'transfer', side_effect=transfer) as move, \
                 patch.object(m.p, 'run'), patch.object(m.h, 'wait_ready'), patch.object(m, 'guest', return_value='{}'), \
                 patch.object(m.syslog_fix, 'nonblocking', return_value=True), contextlib.redirect_stdout(io.StringIO()):
                m.resume_fixture(c, trial, 'f'*64)
                repair.assert_called_once(); self.assertEqual(move.call_count, 2)
            self.assertEqual(original.read_bytes(), saved)
            resumed = json.loads((directory/'resume.json').read_text())
            self.assertEqual(resumed['phase'], 'verified')
            self.assertEqual([x['pool'] for x in resumed['moves']], ['hdd', 'ssd', 'hdd'])

    def test_unexpected_mount_refuses_move(self):
        after = copy.deepcopy(self.before); after['devices']['foreign'] = {'type': 'disk'}
        with patch.object(m.h, 'instance', return_value=after), patch.object(m.p, 'run') as run:
            with self.assertRaises(RuntimeError): m.transfer(self.c, 'developer', self.before, 'hdd')
            run.assert_not_called()

    def test_move_uses_incus_and_preserves_snapshots(self):
        after = copy.deepcopy(self.before); after['devices']['root']['pool'] = 'hdd'
        with patch.object(m.h, 'instance', side_effect=[self.before, after]), patch.object(m.p, 'run') as run:
            m.transfer(self.c, 'developer', self.before, 'hdd')
            self.assertEqual(run.call_args.args[0],
                ['incus','move','local:developer','--storage','hdd','--project','chart-dev'])

    def test_pool_override_is_per_box(self):
        c = {**self.c, 'instance_pools': {'developer': 'hdd'}}
        self.assertEqual(m.p.instance_spec(c, 'developer')['devices']['root']['pool'], 'hdd')
        self.assertEqual(m.p.instance_spec(c, 'other')['devices']['root']['pool'], 'ssd')

    def test_old_and_explicit_configs_validate(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d)/'host.json'
            for c in (self.c, {**self.c, 'instance_pools': {'developer':'hdd'}}):
                file.write_text(json.dumps(c)); self.assertEqual(m.p.config(file), c)
            for mapping in ({'../escape':'hdd'}, {'developer':'foreign'}, []):
                file.write_text(json.dumps({**self.c,'instance_pools':mapping}))
                with self.assertRaises(RuntimeError): m.p.config(file)

    def test_fixture_data_is_retained_on_collision(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m.h, 'host'), patch.object(m.h, 'candidate'), \
             patch.object(m, 'hdd_capacity'), patch.object(m.p, 'query') as query, \
             patch.object(m.p, 'STATE', Path(d)/'state'):
            c = {**self.c,'hdd_mount':d}
            data=Path(d)/'shared-dev/storage-fixtures/trial1'; data.mkdir(parents=True)
            (data/'keep').write_text('original')
            with self.assertRaisesRegex(RuntimeError,'Retained'): m.verify(c, 'trial1', 'f'*64)
            query.assert_not_called(); self.assertEqual((data/'keep').read_text(),'original')

    def test_fixture_program_compiles(self):
        compile(m.FIXTURE, '<fixture>', 'exec')

    def test_plan_never_mutates(self):
        with patch.object(sys,'argv',['storage_move.py','verify','--config','x','--trial','trial1','--image','f'*64]), \
             patch.object(m.p,'config',return_value=self.c), patch.object(m.p,'check_host'), \
             patch.object(m,'verify') as verify, contextlib.redirect_stdout(io.StringIO()):
            m.main(); verify.assert_not_called()


if __name__ == '__main__': unittest.main()

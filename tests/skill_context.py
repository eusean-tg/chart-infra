#!/usr/bin/env python3
"""Isolated checks for the operator skill's read-only context helper."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'skills/chart-infra/scripts/context.py'
context = types.ModuleType('chart_skill_context')
exec(compile(HELPER.read_text(), str(HELPER), 'exec'), context.__dict__)


class ContextTests(unittest.TestCase):
    def test_missing_state_reports_without_creating_state_or_starting_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'uncreated-state'
            output = io.StringIO()
            arguments = ['context.py', '--repo', str(ROOT), '--profile', 'fixture', '--state', str(state)]
            with patch.object(sys, 'argv', arguments), patch.dict(os.environ, {'HOME': temp}), \
                    patch.object(context, 'read_json', side_effect=FileNotFoundError), \
                    patch.object(context.subprocess, 'run') as child, contextlib.redirect_stdout(output):
                context.main()
            result = json.loads(output.getvalue())
            self.assertEqual(result['host_registration'], {'error': 'FileNotFoundError'})
            self.assertEqual(result['storage'], {'error': 'FileNotFoundError'})
            self.assertEqual(result['apps'], {'error': 'FileNotFoundError'})
            self.assertEqual(result['sync'], {'error': 'FileNotFoundError'})
            self.assertEqual(result['cluster'], 'not_checked')
            self.assertFalse(state.exists())
            self.assertEqual(list(root.iterdir()), [])
            child.assert_not_called()

    def test_wrong_cluster_stops_before_profile_queries(self):
        calls = []

        def query(args, env=None):
            calls.append(args)
            if 'nodes' in args:
                return json.dumps({'items': [{'metadata': {'name': 'other-node'}}]})
            if 'namespace' in args and 'kube-system' in args:
                return json.dumps({'metadata': {'uid': 'other-cluster'}})
            self.fail('Profile resources queried after cluster mismatch')

        with patch.object(context, 'command', side_effect=query):
            result = context.cluster_report({'node': 'registered-node', 'kube_system_uid': 'registered-cluster'},
                                            'fixture', {}, {}, {})
        self.assertEqual(result, {'registered_cluster_matches': False})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(args[:3] == ['kubectl', '--request-timeout=10s', 'get'] for args in calls))

    def test_error_details_do_not_leak(self):
        secret = 'synthetic-password-that-must-not-appear'
        errors = [ValueError(secret), OSError(secret),
                  subprocess.CalledProcessError(1, ['fake-command', secret], output=secret, stderr=secret),
                  subprocess.TimeoutExpired(['fake-command', secret], 1, output=secret, stderr=secret)]
        for error in errors:
            with self.subTest(kind=type(error).__name__):
                def fail():
                    raise error
                result = context.attempt(fail)
                self.assertEqual(result, {'error': type(error).__name__})
                self.assertNotIn(secret, json.dumps(result))

    def test_storage_failures_are_observable_without_repairs(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'fixture'
            member = base / 'mongo/default/member-0'
            member.mkdir(parents=True)
            data = base / 'mongo'
            volume_marker = data / '.shared-dev-volume'
            member_marker = member / '.shared-dev-member'
            volume_marker.write_text('synthetic-volume-identity')
            member_marker.write_text('synthetic-member-identity')
            inv = {'profile': 'fixture', 'hdd_uuid': 'expected-disk', 'image': 'expected-image',
                   'volume_marker': volume_marker.read_text(), 'markers': [member_marker.read_text()],
                   'initialized': True}
            expected = {'HDD_UUID': 'expected-disk', 'IMAGE': 'expected-image'}
            with patch.object(context, 'command', return_value='/mnt/hdd expected-disk'):
                result = context.local_storage(base, inv, expected)
                self.assertTrue(result['mount_identity_matches'])
                self.assertTrue(result['volume_marker_matches'])
                self.assertTrue(result['member_marker_matches'])
                self.assertFalse(result['initialized_files_present'])
                self.assertFalse((member / 'WiredTiger').exists())
                (member / 'WiredTiger').write_bytes(b'fixture')
                member_marker.write_text('replacement-member')
                result = context.local_storage(base, inv, expected)
                self.assertTrue(result['initialized_files_present'])
                self.assertFalse(result['member_marker_matches'])
                self.assertEqual(member_marker.read_text(), 'replacement-member')
                self.assertNotIn(inv['volume_marker'], json.dumps(result))
                self.assertNotIn(inv['markers'][0], json.dumps(result))
            with patch.object(context, 'command', return_value='/ unexpected-disk'):
                self.assertFalse(context.local_storage(base, inv, expected)['mount_identity_matches'])

    def test_replaced_volume_is_reported_and_only_get_is_used(self):
        calls = []

        def query(args, env=None):
            calls.append(args)
            kind = args[3]
            if kind == 'nodes':
                return json.dumps({'items': [{'metadata': {'name': 'node'}}]})
            if kind == 'namespace':
                return json.dumps({'metadata': {'uid': 'cluster'}})
            if kind in ('pv', 'pvc'):
                return json.dumps({'metadata': {'uid': 'replacement',
                                  'labels': {'app.kubernetes.io/managed-by': 'chart-fixture'}}})
            if kind == 'pods':
                return json.dumps({'items': []})
            self.fail('Unexpected resource query')

        with patch.object(context, 'command', side_effect=query):
            result = context.cluster_report({'node': 'node', 'kube_system_uid': 'cluster'}, 'fixture',
                                            {'pv_uid': 'retained-pv', 'pvc_uid': 'retained-pvc'}, {}, {})
        self.assertTrue(result['registered_cluster_matches'])
        self.assertFalse(result['retained_volumes']['pv/chart-fixture']['uid_matches'])
        self.assertFalse(result['retained_volumes']['pvc/mongo-data']['uid_matches'])
        self.assertTrue(all(args[:3] == ['kubectl', '--request-timeout=10s', 'get'] for args in calls))


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Offline target mapping and failure guards for image-builder syslog mitigation."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'incus'))
spec = importlib.util.spec_from_file_location('chart_syslog', ROOT / 'incus/syslog_fix.py')
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)


class Syslog(unittest.TestCase):
    def process(self, root, host, guest, namespace, comm='rsyslogd'):
        path = root / str(host); (path / 'ns').mkdir(parents=True)
        (path / 'ns/pid').symlink_to(namespace)
        (path / 'status').write_text(f'Name:\t{comm}\nNSpid:\t{host}\t{guest}\n')
        (path / 'exe').symlink_to('/usr/sbin/' + comm)

    def test_pid_mapping_excludes_host_and_sibling_processes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.process(root, 100, 1, 'pid:[fixture]', 'systemd')
            self.process(root, 200, 238, 'pid:[fixture]')
            self.process(root, 300, 238, 'pid:[sibling]')
            self.process(root, 400, 238, 'pid:[host]')
            self.assertEqual(s.mapped_process(100, 238, root), 200)

    def test_wrong_executable_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.process(root, 100, 1, 'pid:[fixture]', 'systemd')
            self.process(root, 200, 238, 'pid:[fixture]', 'other')
            with self.assertRaisesRegex(RuntimeError, 'not rsyslog'): s.mapped_process(100, 238, root)

    def test_changed_pid_never_signaled(self):
        with patch.object(s.p, 'query', return_value={'status': 'Running', 'pid': 100}), \
             patch.object(s, 'mapped_process', side_effect=[200, 201]), \
             patch.object(s.os, 'pidfd_open', return_value=42), patch.object(s.os, 'close') as close, \
             patch.object(s.select, 'poll') as poll, patch.object(s.signal, 'pidfd_send_signal') as send:
            poll.return_value.poll.return_value = []
            with self.assertRaisesRegex(RuntimeError, 'mapping changed'):
                s.terminate_reader({'project': 'p'}, 'fixture', 238, Mock())
            send.assert_not_called(); close.assert_called_once_with(42)

    def test_term_timeout_has_no_kill_fallback(self):
        with patch.object(s.p, 'query', return_value={'status': 'Running', 'pid': 100}), \
             patch.object(s, 'mapped_process', return_value=200), \
             patch.object(s.os, 'pidfd_open', return_value=42), patch.object(s.os, 'close'), \
             patch.object(s.select, 'poll') as poll, patch.object(s.signal, 'pidfd_send_signal') as send:
            poll.return_value.poll.return_value = []
            with self.assertRaisesRegex(RuntimeError, 'no KILL fallback'):
                s.terminate_reader({'project': 'p'}, 'fixture', 238, Mock())
            send.assert_called_once_with(42, signal.SIGTERM)

    def test_both_reader_flags_required(self):
        self.assertFalse(s.nonblocking({'flags': {'systemd': [os.O_NONBLOCK], 'rsyslog': [0]}}))
        self.assertFalse(s.nonblocking({'flags': {'systemd': [os.O_NONBLOCK]}}))
        self.assertTrue(s.nonblocking({'flags': {'systemd': [os.O_NONBLOCK], 'rsyslog': [os.O_NONBLOCK]}}))

    def test_failed_termination_does_not_start_second_reader(self):
        with tempfile.TemporaryDirectory() as d, patch.object(s, 'guest') as guest, \
             patch.object(s, 'terminate_reader', side_effect=RuntimeError('denied')):
            guest.return_value = json.dumps({'pid': 238})
            receipt = Path(d) / 'proof.json'
            with self.assertRaisesRegex(RuntimeError, 'denied'):
                s.mitigate({'project': 'p'}, 'fixture', Mock(), receipt)
            commands = [call.args[2] for call in guest.call_args_list]
            self.assertNotIn(['systemctl', 'start', 'rsyslog'], commands)
            self.assertEqual(json.loads(receipt.read_text())['failed_step'], 'terminate-rsyslog')

    def test_guest_scripts_compile(self):
        compile(s.PROBE, '<probe>', 'exec'); compile(s.INSTALL, '<install>', 'exec')

if __name__ == '__main__': unittest.main()

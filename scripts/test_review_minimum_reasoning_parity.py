#!/usr/bin/env python3
"""Regress temporary-directory teardown racing fixture-owned workers."""
import os
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from review_minimum_reasoning_parity import fixture_processes, wait_fixture_gone, summarize_branch


class CleanupTests(unittest.TestCase):
    def processes(self, env, identities):
        with patch('pathlib.Path.iterdir', return_value=[Path('/proc/789')]), \
                patch('pathlib.Path.stat', return_value=SimpleNamespace(st_uid=os.getuid())), \
                patch('pathlib.Path.read_bytes', return_value=env), \
                patch('review_minimum_reasoning_parity.process_identity', side_effect=identities):
            return fixture_processes('fixture')

    def test_matching_fixture_waits_despite_process_state_change(self):
        self.assertEqual(self.processes(b'PA_MINIMUM_REASONING_FIXTURE_ID=fixture\0',
            [('proc:123', 'S'), ('proc:123', 'R')]), [(789, 'proc:123')])

    def test_foreign_and_partial_fixture_tags_are_not_owned(self):
        for tag in ('foreign', 'fixture-other'):
            self.assertEqual(self.processes(('PA_MINIMUM_REASONING_FIXTURE_ID=' + tag).encode(),
                [('proc:123', 'S')]), [])

    def test_recycled_and_zombie_processes_are_gone(self):
        for current in [('proc:new', 'S'), ('proc:123', 'Z')]:
            self.assertEqual(self.processes(b'PA_MINIMUM_REASONING_FIXTURE_ID=fixture\0',
                [('proc:123', 'S'), current]), [])

    def test_exit_between_proc_reads_is_gone(self):
        for error in (FileNotFoundError(), ProcessLookupError()):
            self.assertEqual(self.processes(b'', [error]), [])

    def test_unreadable_same_user_process_does_not_claim_cleanup(self):
        with self.assertRaises(PermissionError):
            self.processes(b'', [PermissionError('denied')])

    def test_branch_shutdown_ack_and_control_close_precede_process_wait(self):
        events = []
        connection, process = MagicMock(), MagicMock(returncode=0)
        process.poll.return_value = None
        connection.close.side_effect = lambda: events.append('control_closed')
        process.wait.side_effect = lambda **kwargs: events.append('process_wait')
        reader = connection.makefile.return_value.__enter__.return_value
        reader.readline.side_effect = [json.dumps(row).encode() for row in (
            {'type': 'daemon_hello', 'protocol': {'name': 'prime-agent.daemon', 'version': 7}},
            {'type': 'response', 'id': 'create', 'success': True, 'data': {'id': 'active'}},
            {'type': 'response', 'id': 'branch', 'success': True, 'data': {}},
            {'type': 'response', 'id': 'shutdown', 'success': True, 'data': {}},
        )]
        with tempfile.TemporaryDirectory() as temp, \
                patch('review_minimum_reasoning_parity.subprocess.Popen', return_value=process), \
                patch('review_minimum_reasoning_parity.socket.socket', return_value=connection), \
                patch('review_minimum_reasoning_parity.stop'):
            root = Path(temp)
            session = root / 'session.jsonl'
            session.write_text(json.dumps({'type': 'message', 'id': 'assistant',
                'message': {'role': 'assistant'}}))
            summarize_branch(Path('/fixture/binary'), root, root, {}, session, {})
        commands = [json.loads(call.args[0])['command']
                    for call in connection.sendall.call_args_list]
        self.assertEqual(commands[-1], {'type': 'shutdown', 'force': True})
        self.assertEqual(events, ['control_closed', 'process_wait'])

    def test_waits_for_observed_exit_before_returning(self):
        event = MagicMock()
        with patch('review_minimum_reasoning_parity.fixture_processes',
                   side_effect=[[(789, 'proc:123')], []]) as processes, \
                patch('review_minimum_reasoning_parity.threading.Event', return_value=event):
            wait_fixture_gone('fixture')
            self.assertEqual(processes.call_count, 2)
            event.wait.assert_called_once_with(0.025)

    def test_live_process_at_deadline_remains_a_failure(self):
        with patch('review_minimum_reasoning_parity.fixture_processes',
                   return_value=[(789, 'proc:123')]), \
                patch('review_minimum_reasoning_parity.time.monotonic', side_effect=[0, 36]):
            with self.assertRaisesRegex(AssertionError, 'processes remained'):
                wait_fixture_gone('fixture')


if __name__ == '__main__':
    unittest.main()

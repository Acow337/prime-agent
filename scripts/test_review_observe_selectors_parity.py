"""Guard exact changed-surface receipts without running product binaries."""
import ast
import json
import unittest

import review_observe_selectors_parity as fixture


class SelectorReceiptTests(unittest.TestCase):
    def test_normalized_suffix_is_unique_and_preserves_case_hyphen_probe(self):
        value = fixture.suffix('abcd-00112233', ['abcd-00112233', 'different-99992233'])
        self.assertEqual(value, '0011-2233')
        self.assertEqual(fixture.suffix('abcd-00112233', ['abcd-00112233', 'abcd-00112233']), value)

    def test_colliding_suffix_extends_before_selection(self):
        value = fixture.suffix('abc000112233', ['abc000112233', 'def000112233'])
        normalized = value.replace('-', '').lower()
        self.assertTrue('abc000112233'.endswith(normalized))
        self.assertFalse('def000112233'.endswith(normalized))

    def test_current_probe_rejects_stale_or_duplicate_tool_results(self):
        message = {'role': 'tool', 'tool_call_id': 'observe_current',
                   'content': fixture.MARKER + json.dumps({'actual': True})}
        call = {'role': 'assistant', 'tool_calls': [{'id': 'observe_current'}]}
        requests = [{}, {'messages': [call, message]}]
        self.assertEqual(fixture.current_probe(requests, 'current'), {'actual': True})
        for messages in ([call, {**message, 'tool_call_id': 'observe_previous'}], [call, message, message]):
            with self.assertRaises(AssertionError):
                fixture.current_probe([{}, {'messages': messages}], 'current')

    def test_current_probe_needs_actual_current_assistant_call(self):
        with self.assertRaises(AssertionError):
            fixture.current_probe([{}, {'messages': [{'role': 'tool', 'tool_call_id': 'observe_current',
                'content': fixture.MARKER + '{}'}]}], 'current')

    def test_correct_transcript_and_both_actual_ids_required(self):
        agent = {'activeSessionId': 'actual-a', 'sessionId': 'durable-a'}
        row = {'get': {'value': {'agent': agent}}, 'recent': {'value': {
            'agent': agent, 'messages': [{'role': 'custom', 'customType': 'agent_message', 'text': '[task from parent]\n\nOBSERVE_TASK_A'}]}}}
        expected = {'active': 'actual-a', 'durable': 'durable-a'}
        self.assertEqual(fixture.selected(row, expected, 'OBSERVE_TASK_A'), {
            'selection': 'child-a', 'transcript_marker': 'OBSERVE_TASK_A'})
        row['recent']['value']['messages'].append({'role': 'custom', 'customType': 'agent_message', 'text': '[task from parent]\n\nOBSERVE_TASK_B'})
        with self.assertRaises(AssertionError):
            fixture.selected(row, expected, 'OBSERVE_TASK_A')
        row['recent']['value']['messages'] = [{'role': 'custom', 'customType': 'agent_message', 'text': '[task from parent]\n\nOBSERVE_TASK_B'}]
        with self.assertRaises(AssertionError):
            fixture.selected(row, expected, 'OBSERVE_TASK_A')
        row['get']['value']['agent'] = {'activeSessionId': 'actual-a', 'sessionId': 'wrong-durable'}
        with self.assertRaises(AssertionError):
            fixture.selected(row, expected, 'OBSERVE_TASK_A')

    def test_foreign_error_and_ambiguity_cannot_be_success_or_other_error(self):
        for label, error in (('ts', 'Unknown active session: FFF-123'),
                             ('rust', 'agent suffix is not reachable')):
            row = {'get': {'error': error}, 'recent': {'error': error}}
            self.assertEqual(fixture.rejected(row, 'foreign-family', label), {'rejection': 'foreign-family'})
            row['get'] = {'value': {'agent': {'activeSessionId': 'foreign'}}}
            with self.assertRaises(AssertionError):
                fixture.rejected(row, 'foreign-family', label)
        row = {'get': {'error': 'No live session'}, 'recent': {'error': 'No live session'}}
        with self.assertRaises(AssertionError):
            fixture.rejected(row, 'ambiguous', 'rust')
        row = {operation: {'error': 'Ambiguous active session: matches a,b'} for operation in ('get', 'recent')}
        self.assertEqual(fixture.rejected(row, 'ambiguous', 'ts'), {'rejection': 'ambiguous'})

    def test_child_hold_matches_current_task_not_root_digest_or_history(self):
        self.assertEqual(fixture.child_task({'messages': [{'role': 'user', 'content': '[task from parent]\n\nOBSERVE_TASK_A'}]}), 'OBSERVE_TASK_A')
        self.assertIsNone(fixture.child_task({'messages': [
            {'role': 'user', 'content': 'digest includes OBSERVE_TASK_A'},
            {'role': 'user', 'content': 'Run parent selector baseline.'}]}))
        self.assertIsNone(fixture.child_task({'messages': [{'role': 'assistant', 'content': 'OBSERVE_TASK_A'}]}))

    def test_identical_cell_uses_actual_skill_functions(self):
        code = fixture.cell({'exact': 'actual-a', 'foreign_suffix': 'FFF-123'})
        tree = ast.parse(code)
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)}
        self.assertTrue({'list_agents', 'get_agent', 'recent_messages'} <= calls)
        self.assertIn("'foreign_suffix': 'FFF-123'", code)


if __name__ == '__main__':
    unittest.main()

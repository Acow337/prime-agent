#!/usr/bin/env python3
import copy
import unittest
from review_late_message_receipt_parity import MESSAGE, validate


class LateReceiptGuards(unittest.TestCase):
    def good(self):
        return {'child_result_seen_before_release': True, 'child_request_count': 2,
            'parent_request_count': 3, 'parent_session_id': 'fixture-parent',
            'parent_session_file': '/fixture/parent.jsonl',
            'spawn_handle': {'rlm_child_id': 'fixture-child', 'session_dir': '/fixture/fixture-child'},
            'child_header': {'type': 'session', 'id': 'child-durable', 'parentSession': '/fixture/parent.jsonl'},
            'late_entries': [{'file': '/fixture/fixture-child/child.jsonl', 'entry': {'data': {
                'toolCallId': 'late_child_cell', 'message': {'id': 'fixture-id',
                    'message': MESSAGE, 'receiverRole': 'parent', 'deliveryStatus': 'queued',
                    'target': {'sessionId': 'fixture-parent', 'activeSessionId': 'fixture-parent'}}}}}]}

    def test_actual_late_payload_and_readiness_are_required(self):
        validate(self.good())
        for key, value in [('child_result_seen_before_release', False),
                           ('child_request_count', 1), ('parent_request_count', 1),
                           ('late_entries', [])]:
            changed = {**self.good(), key: value}
            with self.assertRaises(AssertionError):
                validate(changed)
        for key, value in [('message', 'foreign'), ('receiverRole', 'child'),
                           ('deliveryStatus', 'failed'), ('id', '')]:
            changed = self.good()
            changed['late_entries'][0]['entry']['data']['message'][key] = value
            with self.assertRaises(AssertionError):
                validate(changed)
        changed = self.good()
        changed['late_entries'][0]['entry']['data']['message']['target']['sessionId'] = 'foreign'
        with self.assertRaises(AssertionError):
            validate(changed)
        changed = self.good()
        changed['late_entries'].append(copy.deepcopy(changed['late_entries'][0]))
        with self.assertRaises(AssertionError):
            validate(changed)
        changed = self.good()
        changed['late_entries'][0]['file'] = '/fixture/foreign/child.jsonl'
        with self.assertRaises(AssertionError):
            validate(changed)
        changed = self.good()
        changed['child_header']['parentSession'] = '/fixture/foreign-parent.jsonl'
        with self.assertRaises(AssertionError):
            validate(changed)
        changed = self.good()
        changed['late_entries'][0]['entry']['data']['toolCallId'] = 'historical' 
        with self.assertRaises(AssertionError):
            validate(changed)


if __name__ == '__main__':
    unittest.main()

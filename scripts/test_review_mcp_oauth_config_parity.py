#!/usr/bin/env python3
import copy
import json
import unittest
from review_mcp_oauth_config_parity import BEFORE, AFTER, DEFAULT, MARKER, extract, validate


class OAuthConfigGuards(unittest.TestCase):
    def test_fields_reload_and_default_absence_are_exact(self):
        good = {'before': {'fixture-oauth': BEFORE, 'fixture-default': DEFAULT},
                'after': {'fixture-oauth': AFTER, 'fixture-default': DEFAULT}}
        validate(good)
        for phase in ('before', 'after'):
            for key in ('clientId', 'callbackPort'):
                changed = copy.deepcopy(good)
                del changed[phase]['fixture-oauth'][key]
                with self.assertRaises(AssertionError):
                    validate(changed)
        changed = copy.deepcopy(good)
        changed['after'] = changed['before']
        with self.assertRaises(AssertionError):
            validate(changed)
        changed = copy.deepcopy(good)
        changed['before']['fixture-default']['callbackPort'] = 0
        with self.assertRaises(AssertionError):
            validate(changed)

    def test_current_real_tool_result_is_required(self):
        value = {'fixture-oauth': BEFORE, 'fixture-default': DEFAULT}
        tool = {'role': 'tool', 'tool_call_id': 'oauth_config_before',
                'content': MARKER + json.dumps(value)}
        self.assertEqual(extract([{}, {'messages': [tool]}], 'before'), value)
        for rows in [[{'messages': [tool]}, {'messages': []}],
                     [{}, {'messages': [tool, tool]}],
                     [{}, {'messages': [{**tool, 'tool_call_id': 'historical'}]}]]:
            with self.assertRaises(AssertionError):
                extract(rows, 'before')


if __name__ == '__main__':
    unittest.main()

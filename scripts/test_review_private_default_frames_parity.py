"""Guard actual-startup receipt acceptance without choosing model priority."""
import tempfile
from pathlib import Path
import unittest

from review_private_default_frames_parity import (
    PUBLIC, PRIVATE, WARNING, Screen, validate_observation, write_frame,
)


def observation(label, scenario):
    warn = label == 'rust' and scenario == 'unavailable'
    return {'selected_model': PUBLIC,
            'available_ids': [PUBLIC] if scenario == 'unavailable' else [PUBLIC, PRIVATE],
            'fallback_message': WARNING + ' Using prime-inference/' + PUBLIC if warn else None,
            'frames': {f'{columns}x{rows}': {'columns': columns, 'rows': rows,
                       'input_preserved': True, 'warning_prefix_count': int(warn), 'warning_text_complete': True}
                       for columns, rows in ((120, 40), (80, 28))}}


class CaptureGuards(unittest.TestCase):
    def test_characterization_does_not_approve_either_priority_policy(self):
        for label in ('ts', 'rust'):
            for selected in (PUBLIC, PRIVATE):
                value = observation(label, 'authorized')
                value['selected_model'] = selected
                validate_observation(label, 'authorized', value)

    def test_unavailable_or_excluded_private_model_is_rejected(self):
        for scenario in ('unavailable', 'excluded'):
            value = observation('rust', scenario)
            value['selected_model'] = PRIVATE
            with self.assertRaisesRegex(AssertionError, 'unavailable or excluded'):
                validate_observation('rust', scenario, value)

    def test_mismatched_authorization_state_is_rejected(self):
        for scenario in ('authorized', 'unavailable'):
            value = observation('rust', scenario)
            value['available_ids'] = [PUBLIC] if scenario == 'authorized' else [PUBLIC, PRIVATE]
            with self.assertRaisesRegex(AssertionError, 'authorization fixture'):
                validate_observation('rust', scenario, value)

    def test_missing_duplicate_warning_or_lost_input_is_rejected(self):
        for field, replacement in (('warning_prefix_count', 0), ('warning_prefix_count', 2),
                                   ('input_preserved', False), ('columns', 119), ('warning_text_complete', False)):
            value = observation('rust', 'unavailable')
            value['frames']['120x40'][field] = replacement
            with self.assertRaises(AssertionError):
                validate_observation('rust', 'unavailable', value)

    def test_unexpected_warning_in_ts_or_excluded_case_is_rejected(self):
        for label, scenario in (('ts', 'unavailable'), ('rust', 'excluded')):
            value = observation(label, scenario)
            value['fallback_message'] = WARNING
            with self.assertRaisesRegex(AssertionError, 'unexpected warning'):
                validate_observation(label, scenario, value)

    def test_svg_escapes_real_cells_and_retains_matched_dimensions(self):
        screen = Screen(120, 40)
        screen.feed(b'<fixture>&')
        terminal = type('CapturedTerminal', (), {'screen': screen, 'raw': b'<fixture>&'})()
        with tempfile.TemporaryDirectory() as temp:
            frame = write_frame(Path(temp), 'capture', terminal)
            self.assertEqual((frame['columns'], frame['rows']), (120, 40))
            svg = (Path(temp) / 'capture.svg').read_text()
            self.assertIn('&lt;fixture&gt;&amp;', svg)
            self.assertNotIn('<fixture>', svg)


if __name__ == '__main__':
    unittest.main()

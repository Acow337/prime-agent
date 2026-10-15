#!/usr/bin/env python3
"""Mock-only evidence guards. Never starts a product binary."""
import copy
import unittest
from unittest.mock import Mock, patch

from review_at_completion_parity import (
    CASES, children_visible, close_terminal, compare, completion_rows, frame,
)
from review_dock_screen import DockScreen


def observations():
    side = {'behavior_verified': True, 'cleanup_verified': True, 'inputs': ['40646972', '09'],
            'frames': {'after': {'text': 'only_child.rs', 'menu_rows': ['only_child.rs'],
                                 'children_visible': True}}}
    return {'runs': {kind: {'cases': {case: copy.deepcopy(side) for case in CASES}}
                     for kind in ('ts', 'rust')}}


class EvidenceGuards(unittest.TestCase):
    def test_editor_token_does_not_count_as_a_child_menu(self):
        text = '│ @"dir with spaces/only_child.rs"\nmodel · 0 tokens'
        self.assertFalse(children_visible(text))
        self.assertEqual(completion_rows(text), [])

    def test_actual_menu_row_counts_beside_quoted_editor(self):
        self.assertTrue(children_visible('› only_child.rs   dir with spaces/only_child.rs\n'
                                         '│ @"dir with spaces/"'))

    def test_directory_row_alone_is_not_a_child(self):
        self.assertFalse(children_visible('› dir with spaces/   dir with spaces\n│ @dir'))

    def test_intended_ts_closed_menu_is_reported_without_normalization(self):
        receipt = observations()
        for case in CASES:
            receipt['runs']['ts']['cases'][case]['frames']['after'].update(
                children_visible=False, menu_rows=[], text='│ @"dir with spaces/"')
        result = compare(receipt)
        self.assertTrue(all(not row['ts_children_visible'] and row['rust_children_visible']
                            and not row['full_text_equal'] and not row['menu_rows_equal']
                            for row in result.values()))

    def test_missing_rust_children_fails_even_when_other_phases_pass(self):
        receipt = observations()
        receipt['runs']['rust']['cases']['enter']['frames']['after']['children_visible'] = False
        with self.assertRaises(AssertionError):
            compare(receipt)

    def test_unexecuted_case_cannot_pass(self):
        receipt = observations()
        receipt['runs']['ts']['cases']['tab']['behavior_verified'] = False
        with self.assertRaises(AssertionError):
            compare(receipt)

    def test_failed_cleanup_cannot_pass(self):
        receipt = observations()
        receipt['runs']['rust']['cases']['typed_slash']['cleanup_verified'] = False
        with self.assertRaises(AssertionError):
            compare(receipt)

    def test_caught_case_error_cannot_pass(self):
        receipt = observations()
        receipt['runs']['ts']['cases']['enter']['error'] = 'capture failed'
        with self.assertRaises(AssertionError):
            compare(receipt)

    def test_different_keys_cannot_claim_comparison(self):
        receipt = observations()
        receipt['runs']['ts']['cases']['tab']['inputs'] = ['different']
        with self.assertRaises(AssertionError):
            compare(receipt)

    def test_unknown_terminal_geometry_cannot_pass_frame_evidence(self):
        screen = DockScreen()
        screen.feed(b'\x1b[3S')
        with self.assertRaises(AssertionError):
            frame(Mock(screen=screen, raw=bytearray()))

    def test_cleanup_fallback_remains_red_and_joins_peer(self):
        terminal = Mock(raw=bytearray(), master=7)
        terminal.process.poll.return_value = None
        scope = Mock(targets={})
        scope.live.return_value = []
        server = Mock(commands=[], errors=[])
        observed = {}
        with patch('review_at_completion_parity.time.monotonic', side_effect=[0, 20]), \
                patch('review_at_completion_parity.os.close'):
            with self.assertRaisesRegex(AssertionError, 'ordinary TUI exit'):
                close_terminal(terminal, scope, server, observed)
        self.assertFalse(observed['cleanup_verified'])
        server.close.assert_called_once()

    def test_peer_still_joins_when_pty_close_fails(self):
        terminal = Mock(raw=bytearray(), master=7)
        terminal.process.poll.return_value = 0
        scope = Mock(targets={})
        server = Mock(commands=[], errors=[])
        observed = {}
        with patch('review_at_completion_parity.os.close', side_effect=OSError('close failed')):
            with self.assertRaisesRegex(AssertionError, 'PTY close'):
                close_terminal(terminal, scope, server, observed)
        self.assertFalse(observed['cleanup_verified'])
        server.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()

import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terminal import Terminal

# Real process boundary: raw mode and labels must not count as an accepting editor.
EDITOR = r'''
import os, sys, tty
tty.setraw(0)
os.write(1, b'Search sessions\r\n> ')
text = ''
while True:
    char = os.read(0, 1).decode()
    if char == '\x03': break
    if char == '\x7f': text = text[:-1]
    else: text += char
    os.write(1, ('\x1b[2;1H\x1b[2K> ' + text + '\x1b[2;' + str(3+len(text)) + 'H').encode())
'''


class TerminalTests(unittest.TestCase):
    def test_echo_and_cleanup_through_real_pty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Terminal([sys.executable, "-c", EDITOR], root, os.environ.copy(), root / "echo")
            try:
                self.assertGreater(app.ready(3), 0)
                self.assertEqual(app.display.screen.cursor.x, 2)
            finally:
                app.close()
            self.assertIn("Search sessions", (root / "echo.raw").read_text())
            self.assertFalse(app.child.isalive())

    def test_exited_process_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Terminal([sys.executable, "-c", "print('Search sessions')"], root,
                           os.environ.copy(), root / "failed")
            try:
                with self.assertRaises(RuntimeError):
                    app.ready(1)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()

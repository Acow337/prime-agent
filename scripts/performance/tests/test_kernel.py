import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel_bench import measure


class KernelTests(unittest.TestCase):
    def test_real_runtime_shell_output_interrupt_file_io_and_restored_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = measure(Path(sys.executable), Path(temporary))
            self.assertGreater(report["metrics"]["kernel_start_ms"], 0)
            self.assertGreater(report["metrics"]["restore_ms"], 0)
            protocol = (Path(temporary) / "kernel.jsonl").read_text()
            self.assertIn("KeyboardInterrupt", protocol)
            self.assertTrue(any(json.loads(line).get("status") == "ok" for line in protocol.splitlines()))
            self.assertTrue((Path(temporary) / "restore.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

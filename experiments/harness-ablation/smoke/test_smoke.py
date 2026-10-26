import json
import sys
import tempfile
import unittest
from pathlib import Path


SMOKE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SMOKE_DIR))

import tasks


class TaskFixtureTests(unittest.TestCase):
    def test_materialization_is_deterministic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = tasks.materialize_tasks(Path(first))
            tasks.materialize_tasks(Path(second))
            self.assertEqual(
                [case.task_id for case in left],
                ["t1-structured", "t2-log-correlation", "t3-parser-fix"],
            )
            self.assertEqual(
                tasks.fixture_digest(Path(first)), tasks.fixture_digest(Path(second))
            )

    def test_json_reference_answers_pass_and_malformed_answers_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = tasks.materialize_tasks(Path(directory))
            for case in cases[:2]:
                with self.subTest(case=case.task_id):
                    result = tasks.verify(case, case.reference_output)
                    self.assertEqual((result["score"], result["success"]), (1.0, True))
                    self.assertFalse(tasks.verify(case, "not valid output")["success"])

    def test_parser_fixture_starts_red_and_reference_fix_turns_it_green(self):
        with tempfile.TemporaryDirectory() as directory:
            case = tasks.materialize_tasks(Path(directory))[2]
            self.assertFalse(tasks.verify(case, "")["success"])
            tasks.apply_reference_fix(case)
            result = tasks.verify(case, "fixed")
            self.assertEqual((result["score"], result["success"]), (1.0, True))

    def test_log_fixture_is_approximately_ten_kilobytes(self):
        with tempfile.TemporaryDirectory() as directory:
            case = tasks.materialize_tasks(Path(directory))[1]
            size = (case.workdir / "events.log").stat().st_size
            self.assertGreaterEqual(size, 9_000)
            self.assertLessEqual(size, 13_000)


class ManifestTests(unittest.TestCase):
    def test_budget_values_match_the_approved_design(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        self.assertEqual(manifest["max_provider_requests"], 6)
        self.assertEqual(manifest["response_tokens"], 1024)
        self.assertEqual(manifest["hard_budget_cny"], 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

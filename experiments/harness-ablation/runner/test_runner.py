"""Offline tests for the ablation matrix runner and the I-family tasks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "families"))

import e_family  # noqa: E402
import t_family  # noqa: E402
import i_family  # noqa: E402
import runner  # noqa: E402


class IFamilyTests(unittest.TestCase):
    def test_materialization_is_deterministic_and_answers_verify(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = i_family.materialize(1, Path(first))
            right = i_family.materialize(1, Path(second))
            for name in ("orders.csv", "customers.csv", "refunds.csv"):
                self.assertEqual(
                    (left.workdir / name).read_bytes(),
                    (right.workdir / name).read_bytes(),
                )
            self.assertEqual(left.expected, right.expected)
            self.assertTrue(i_family.verify(left, left.reference)["success"])
            self.assertEqual(i_family.verify(left, left.reference)["score"], 1.0)

    def test_wrong_and_unparseable_answers_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            case = i_family.materialize(2, Path(directory))
        self.assertEqual(i_family.verify(case, "not json")["score"], 0.0)
        wrong = dict(case.expected, revenue_cents=case.expected["revenue_cents"] + 10)
        result = i_family.verify(case, json.dumps(wrong))
        self.assertFalse(result["success"])
        self.assertAlmostEqual(result["score"], 5 / 6)

    def test_instances_differ(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one = i_family.materialize(1, root)
            two = i_family.materialize(2, root)
        self.assertNotEqual(one.expected, two.expected)


class EFamilyTests(unittest.TestCase):
    def test_buggy_repo_fails_and_reference_fix_turns_it_green(self):
        with tempfile.TemporaryDirectory() as directory:
            case = e_family.materialize(1, Path(directory))
            self.assertEqual(e_family.verify(case, "")["score"], 0.0)
            self.assertTrue(e_family.dry_run_verify(case)["success"])

    def test_instances_vary_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = (e_family.materialize(1, root).workdir / "config.py").read_text()
            second = (e_family.materialize(4, root).workdir / "config.py").read_text()
        self.assertNotEqual(first, second)


class TFamilyTests(unittest.TestCase):
    def test_series_has_three_episodes_with_one_learning_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_family.materialize(1, Path(directory))
        self.assertEqual([episode.name for episode in case.episodes], ["ep1", "ep2", "ep3"])
        self.assertEqual([episode.scored for episode in case.episodes], [False, True, True])
        self.assertNotIn("error_rate_pct >= 5", case.episodes[1].prompt)
        self.assertNotIn("Triage rule:", case.episodes[1].prompt)

    def test_reference_answers_score_one_and_wrong_labels_score_lower(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_family.materialize(1, Path(directory))
        for index, episode in enumerate(case.episodes):
            self.assertTrue(t_family.verify_episode(case, index, episode.reference)["success"])
        self.assertTrue(t_family.dry_run_verify(case)["success"])
        wrong = {"items": {}}
        self.assertEqual(t_family.verify_episode(case, 1, json.dumps(wrong))["score"], 0.0)

    def test_episode_three_uses_the_changed_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_family.materialize(1, Path(directory))
        import json as _json

        old = _json.loads(case.episodes[1].reference)["items"]
        new = _json.loads(case.episodes[2].reference)["items"]
        self.assertNotEqual(old, new)


class MatrixRunnerTests(unittest.TestCase):
    def _config(self) -> dict:
        config = json.loads((HERE / "config.pilot.json").read_text(encoding="utf-8"))
        config["families"] = {"i": [1, 2]}
        config["mode"] = "dry-run"
        return config

    def test_dry_run_walks_every_cell_and_summarizes(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results"
            records = runner.run_matrix(
                config, Path("unused-binary"), results, "dry-run"
            )
            self.assertEqual(len(records), 2 * len(runner.TREATMENTS))
            self.assertEqual(len({record["run_id"] for record in records}), len(records))
            self.assertTrue(all(record["status"] == "completed" for record in records))
            self.assertTrue(all(record["success"] for record in records))
            summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["counts"]["completed"], len(records))
            self.assertEqual(set(summary["treatments"]), set(runner.TREATMENTS))
            for treatment in summary["treatments"].values():
                self.assertEqual(treatment["success_rate"], 1.0)
            self.assertEqual(summary["usage"]["actual_cost_cny"], 0.0)

    def test_resume_skips_completed_cells(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results"
            first = runner.run_matrix(config, Path("unused"), results, "dry-run")
            second = runner.run_matrix(config, Path("unused"), results, "dry-run")
            self.assertEqual(len(first), 16)
            self.assertEqual(second, [])
            lines = (results / "records.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 16)

    def test_planned_matrix_covers_models_treatments_and_instances(self):
        config = self._config()
        config["models"] = {"cheap": "deepseek-v4-flash", "strong": "deepseek-v4-pro"}
        runs = runner.planned_runs(config)
        self.assertEqual(len(runs), 2 * 2 * len(runner.TREATMENTS))
        self.assertEqual(
            {run["treatment"] for run in runs}, set(runner.TREATMENTS)
        )
        self.assertEqual({run["task_id"] for run in runs}, {"i-01", "i-02"})


if __name__ == "__main__":
    unittest.main()

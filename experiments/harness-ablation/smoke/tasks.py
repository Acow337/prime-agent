"""Deterministic smoke-task fixtures and verifiers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskCase:
    task_id: str
    workdir: Path
    prompt: str
    kind: str
    expected: dict[str, Any]
    reference_output: str


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _structured_case(root: Path) -> TaskCase:
    workdir = root / "t1-structured"
    workdir.mkdir(parents=True, exist_ok=True)
    expected = {
        "successful_units": 31,
        "alpha_successful_units": 17,
        "successes_before_10": 2,
        "highest_success_team": "alpha",
    }
    prompt = """Solve this deterministic aggregation task. Do not use external data.

| id | team  | time  | status  | units |
| A  | alpha | 09:10 | success | 7     |
| B  | beta  | 09:20 | failed  | 5     |
| C  | alpha | 09:35 | success | 4     |
| D  | beta  | 10:05 | success | 11    |
| E  | alpha | 10:20 | success | 6     |
| F  | beta  | 10:45 | success | 3     |

Return exactly one JSON object with keys successful_units,
alpha_successful_units, successes_before_10 (09:00 <= time < 10:00), and
highest_success_team. No prose or Markdown.
"""
    reference = json.dumps(expected, sort_keys=True)
    return TaskCase("t1-structured", workdir, prompt, "json", expected, reference)


def _log_lines() -> list[str]:
    lines = []
    for index in range(100):
        lines.append(
            f"2026-10-23T08:{index % 60:02d}:{index % 59:02d}Z level=INFO "
            f"request=noise-{index:03d} account=acct-{(index * 7) % 91:02d} "
            f"state=healthy shard={(index * 11) % 17:02d} note=background-scan-{index:03d}"
        )
    lines[17] = (
        "2026-10-23T09:01:02Z level=INFO request=req-042 correlation=corr-blue-7 "
        "account=acct-17 event=accepted source=edge"
    )
    lines[43] = (
        "2026-10-23T09:01:09Z level=WARN request=req-042 correlation=corr-blue-7 "
        "account=acct-71 event=lookup status=stale superseded_by=seq-44"
    )
    lines[44] = (
        "2026-10-23T09:01:10Z level=INFO request=req-042 correlation=corr-blue-7 "
        "account=acct-17 event=lookup status=canonical sequence=44"
    )
    lines[88] = (
        "2026-10-23T09:01:31Z level=ERROR request=req-042 correlation=corr-blue-7 "
        "account=acct-17 event=retry cause=expired_lease attempt=2"
    )
    lines[95] = (
        "2026-10-23T09:02:04Z level=INFO request=req-042 correlation=corr-blue-7 "
        "account=acct-17 event=settled status=recovered terminal=true"
    )
    return lines


def _log_case(root: Path) -> TaskCase:
    workdir = root / "t2-log-correlation"
    workdir.mkdir(parents=True, exist_ok=True)
    _write(workdir / "events.log", "\n".join(_log_lines()) + "\n")
    expected = {
        "canonical_account": "acct-17",
        "root_cause": "expired_lease",
        "terminal_status": "recovered",
    }
    prompt = """Read events.log in the current directory. For request req-042 and
correlation corr-blue-7, resolve stale/superseded records and report the
canonical account, the retry root cause, and the terminal status. Return exactly
one JSON object with keys canonical_account, root_cause, and terminal_status.
No prose or Markdown. Do not use external data.
"""
    reference = json.dumps(expected, sort_keys=True)
    return TaskCase("t2-log-correlation", workdir, prompt, "json", expected, reference)


_BROKEN_PARSER = r'''def parse_record(text: str) -> list[str]:
    """Split pipe fields while allowing a backslash to escape the next byte."""
    fields = []
    current = []
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            if current:
                fields.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        fields.append("".join(current))
    return fields
'''


_FIXED_PARSER = r'''def parse_record(text: str) -> list[str]:
    """Split pipe fields while allowing a backslash to escape the next byte."""
    fields = []
    current = []
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields
'''


def _parser_case(root: Path) -> TaskCase:
    workdir = root / "t3-parser-fix"
    workdir.mkdir(parents=True, exist_ok=True)
    _write(workdir / "parser.py", _BROKEN_PARSER)
    _write(
        workdir / "test_parser.py",
        r'''import unittest
from parser import parse_record


class ParserPublicTests(unittest.TestCase):
    def test_escaped_separator(self):
        self.assertEqual(parse_record(r"alpha|beta\|gamma|delta"),
                         ["alpha", "beta|gamma", "delta"])


if __name__ == "__main__":
    unittest.main()
''',
    )
    _write(
        workdir / "hidden_test_parser.py",
        r'''import unittest
from parser import parse_record


class ParserHiddenTests(unittest.TestCase):
    def test_empty_and_trailing_fields(self):
        self.assertEqual(parse_record(r"alpha||beta\|gamma|"),
                         ["alpha", "", "beta|gamma", ""])

    def test_trailing_escape_is_literal(self):
        self.assertEqual(parse_record("alpha|beta\\"), ["alpha", "beta\\"])


if __name__ == "__main__":
    unittest.main()
''',
    )
    prompt = """Fix the bug in parser.py. Empty fields, including a trailing empty
field, must be preserved; a trailing backslash must be literal. Keep escaped
separators working. Run `python3 -m unittest test_parser.py -v`. Do not inspect
files whose names start with hidden_. Make the smallest correct code change and
finish with a concise summary.
"""
    return TaskCase("t3-parser-fix", workdir, prompt, "tests", {}, "fixed")


def materialize_tasks(root: Path) -> list[TaskCase]:
    root.mkdir(parents=True, exist_ok=True)
    return [_structured_case(root), _log_case(root), _parser_case(root)]


def fixture_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def _verify_json(case: TaskCase, output: str) -> dict[str, Any]:
    objects = _json_objects(output)
    if not objects:
        return {"success": False, "score": 0.0, "reason": "no JSON object"}
    answer = objects[-1]
    correct = sum(answer.get(key) == value for key, value in case.expected.items())
    score = correct / len(case.expected)
    return {
        "success": score == 1.0 and set(answer) == set(case.expected),
        "score": score,
        "answer": answer,
    }


def _run_tests(workdir: Path, module: str) -> bool:
    result = subprocess.run(
        ["python3", "-m", "unittest", "-v", module],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return result.returncode == 0


def _verify_parser(case: TaskCase) -> dict[str, Any]:
    public = _run_tests(case.workdir, "test_parser.py")
    hidden = _run_tests(case.workdir, "hidden_test_parser.py")
    score = (int(public) + int(hidden)) / 2
    return {"success": public and hidden, "score": score, "public": public, "hidden": hidden}


def verify(case: TaskCase, output: str) -> dict[str, Any]:
    if case.kind == "json":
        return _verify_json(case, output)
    if case.kind == "tests":
        return _verify_parser(case)
    raise ValueError(f"unknown task kind: {case.kind}")


def apply_reference_fix(case: TaskCase) -> None:
    if case.task_id != "t3-parser-fix":
        raise ValueError("reference fix only applies to t3-parser-fix")
    _write(case.workdir / "parser.py", _FIXED_PARSER)

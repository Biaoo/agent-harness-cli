from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


PASS_CHECK = """
from __future__ import annotations

import argparse
import json


parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
args = parser.parse_args()
with open(args.input, "r", encoding="utf-8") as handle:
    data = json.load(handle)
name = data["check"].get("name", "pass_check")
severity = data["check"].get("severity", "error")
print(json.dumps({
    "check": name,
    "passed": True,
    "severity": severity,
    "summary": f"{name} passed.",
    "score": 1.0,
    "reasons": []
}))
"""


FAIL_CHECK = """
from __future__ import annotations

import argparse
import json


parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
args = parser.parse_args()
with open(args.input, "r", encoding="utf-8") as handle:
    data = json.load(handle)
name = data["check"].get("name", "fail_check")
severity = data["check"].get("severity", "error")
print(json.dumps({
    "check": name,
    "passed": False,
    "severity": severity,
    "summary": "Synthetic failure.",
    "score": 0.0,
    "reasons": [{
        "message": "Synthetic failure for test coverage.",
        "suggestion": "Fix the synthetic issue.",
        "requires_user_input": False,
        "evidence": {"source": "test"}
    }]
}))
"""


class HarnessCliTests(unittest.TestCase):
    def run_cli(self, *args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agent_harness_cli.cli", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )

    def make_temp_project(self, temp_root: Path) -> tuple[Path, Path, Path]:
        task_dir = temp_root / "tasks"
        checks_dir = temp_root / "checks"
        report_dir = temp_root / "reports"
        task_dir.mkdir()
        checks_dir.mkdir()
        report_dir.mkdir()
        pass_check = checks_dir / "pass_check.py"
        fail_check = checks_dir / "fail_check.py"
        pass_check.write_text(textwrap.dedent(PASS_CHECK), encoding="utf-8")
        fail_check.write_text(textwrap.dedent(FAIL_CHECK), encoding="utf-8")
        return task_dir, report_dir, checks_dir

    def write_task(self, task_path: Path, checks: list[dict[str, object]]) -> None:
        task_path.write_text(
            json.dumps(
                {
                    "id": task_path.stem,
                    "description": "Temporary harness test task.",
                    "checks": checks,
                }
            ),
            encoding="utf-8",
        )

    def test_run_checks_writes_report_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            task_path = task_dir / "passing_task.json"
            self.write_task(
                task_path,
                [
                    {
                        "name": "first_check",
                        "command": ["{python}", str(checks_dir / "pass_check.py")],
                        "severity": "error",
                    },
                    {
                        "name": "second_check",
                        "command": ["{python}", str(checks_dir / "pass_check.py")],
                        "severity": "warning",
                    },
                ],
            )

            completed = self.run_cli(
                "run-checks",
                "--task",
                str(task_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "sample-report",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("PASSED 2/2 checks", completed.stdout)
            self.assertIn("report_id: sample-report", completed.stdout)
            report_path = report_dir / "sample-report.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["summary"]["total_checks"], 2)

    def test_blocking_check_failure_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            task_path = task_dir / "failing_task.json"
            self.write_task(
                task_path,
                [
                    {
                        "name": "failing_check",
                        "command": ["{python}", str(checks_dir / "fail_check.py")],
                        "severity": "error",
                    }
                ],
            )

            completed = self.run_cli(
                "run-checks",
                "--task",
                str(task_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "failure-report",
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("FAILED 1/1 checks", completed.stdout)
            report = json.loads((report_dir / "failure-report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["checks"][0]["check"], "failing_check")
            self.assertEqual(report["next_actions"], ["Fix the synthetic issue."])

    def test_view_paginates_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            task_path = task_dir / "page_task.json"
            self.write_task(
                task_path,
                [
                    {
                        "name": "first_check",
                        "command": ["{python}", str(checks_dir / "pass_check.py")],
                        "severity": "error",
                    },
                    {
                        "name": "second_check",
                        "command": ["{python}", str(checks_dir / "pass_check.py")],
                        "severity": "error",
                    },
                    {
                        "name": "third_check",
                        "command": ["{python}", str(checks_dir / "pass_check.py")],
                        "severity": "warning",
                    },
                ],
            )

            run_completed = self.run_cli(
                "run-checks",
                "--task",
                str(task_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "page-report",
            )
            self.assertEqual(run_completed.returncode, 0, run_completed.stderr + run_completed.stdout)

            view_completed = self.run_cli(
                "view",
                "page-report",
                "--report-dir",
                str(report_dir),
                "--page-size",
                "2",
            )

            self.assertEqual(view_completed.returncode, 0, view_completed.stderr + view_completed.stdout)
            self.assertIn("report_id: page-report", view_completed.stdout)
            self.assertIn("page: 1/2", view_completed.stdout)
            self.assertIn(
                f"next: agent-harness view page-report --page 2 --page-size 2 --report-dir {report_dir}",
                view_completed.stdout,
            )


if __name__ == "__main__":
    unittest.main()

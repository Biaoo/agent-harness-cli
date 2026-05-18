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

    def write_workflow(self, workflow_path: Path, workflow: dict[str, object]) -> None:
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

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

    def test_workflow_step_advances_after_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            workflow_path = Path(temp_dir) / "workflows" / "research.json"
            state_path = Path(temp_dir) / ".agent-harness" / "state.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "research_flow",
                    "version": 1,
                    "initial": "goal_analysis",
                    "nodes": [
                        {
                            "id": "goal_analysis",
                            "type": "stage",
                            "title": "Goal analysis",
                            "checks": [
                                {
                                    "name": "goal_ready",
                                    "command": ["{python}", str(checks_dir / "pass_check.py")],
                                    "severity": "error",
                                }
                            ],
                            "transitions": [
                                {
                                    "id": "collect_literature",
                                    "when": "passed",
                                    "action": "advance",
                                    "to": "literature_collection",
                                    "prompt": "Collect relevant literature next.",
                                }
                            ],
                        },
                        {"id": "literature_collection", "type": "terminal", "title": "Literature collection"},
                    ],
                },
            )

            completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-pass",
                "--hook-json",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            hook = json.loads(completed.stdout)
            self.assertEqual(hook["decision"], "block")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["active"], ["literature_collection"])
            self.assertEqual(state["completed"], ["goal_analysis"])
            report = json.loads((report_dir / "workflow-pass.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["nodes"][0]["transition"]["id"], "collect_literature")

    def test_workflow_step_blocks_on_failure_without_advancing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            workflow_path = Path(temp_dir) / "workflows" / "research.json"
            state_path = Path(temp_dir) / ".agent-harness" / "state.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "research_flow",
                    "version": 1,
                    "initial": "goal_analysis",
                    "nodes": [
                        {
                            "id": "goal_analysis",
                            "type": "stage",
                            "checks": [
                                {
                                    "name": "goal_ready",
                                    "command": ["{python}", str(checks_dir / "fail_check.py")],
                                    "severity": "error",
                                }
                            ],
                            "transitions": [
                                {
                                    "id": "collect_literature",
                                    "when": "passed",
                                    "action": "advance",
                                    "to": "literature_collection",
                                }
                            ],
                        },
                        {"id": "literature_collection", "type": "terminal"},
                    ],
                },
            )

            completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-fail",
                "--hook-json",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            hook = json.loads(completed.stdout)
            self.assertEqual(hook["decision"], "block")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["active"], ["goal_analysis"])
            self.assertEqual(state["completed"], [])
            self.assertEqual(state["status"], "failed")
            report = json.loads((report_dir / "workflow-fail.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["summary"]["blocking_failures"], 1)

    def test_workflow_warning_failure_still_matches_passed_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            workflow_path = Path(temp_dir) / "workflows" / "research.json"
            state_path = Path(temp_dir) / ".agent-harness" / "state.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "research_flow",
                    "version": 1,
                    "initial": "chart_review",
                    "nodes": [
                        {
                            "id": "chart_review",
                            "type": "stage",
                            "checks": [
                                {
                                    "name": "chart_polish",
                                    "command": ["{python}", str(checks_dir / "fail_check.py")],
                                    "severity": "warning",
                                }
                            ],
                            "transitions": [
                                {
                                    "id": "write_paper",
                                    "when": "passed",
                                    "action": "advance",
                                    "to": "paper_writing",
                                }
                            ],
                        },
                        {"id": "paper_writing", "type": "terminal"},
                    ],
                },
            )

            completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-warning",
                "--hook-json",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["active"], ["paper_writing"])
            self.assertEqual(state["completed"], ["chart_review"])
            report = json.loads((report_dir / "workflow-warning.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["summary"]["warning_failures"], 1)

    def test_workflow_model_choice_can_choose_next_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, report_dir, checks_dir = self.make_temp_project(Path(temp_dir))
            workflow_path = Path(temp_dir) / "workflows" / "research.json"
            state_path = Path(temp_dir) / ".agent-harness" / "state.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "research_flow",
                    "version": 1,
                    "initial": "information_completeness_review",
                    "nodes": [
                        {
                            "id": "information_completeness_review",
                            "type": "gate",
                            "checks": [
                                {
                                    "name": "source_coverage",
                                    "command": ["{python}", str(checks_dir / "pass_check.py")],
                                    "severity": "error",
                                }
                            ],
                            "decision_policy": {"mode": "model_choice", "require_reason": True},
                            "transitions": [
                                {
                                    "id": "collect_more_sources",
                                    "when": "passed",
                                    "action": "advance",
                                    "to": "literature_collection",
                                    "label": "Collect more sources",
                                },
                                {
                                    "id": "start_data_processing",
                                    "when": "passed",
                                    "action": "advance",
                                    "to": "data_processing",
                                    "label": "Start data processing",
                                },
                            ],
                        },
                        {"id": "literature_collection", "type": "terminal"},
                        {"id": "data_processing", "type": "terminal"},
                    ],
                },
            )

            step_completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-choice",
                "--hook-json",
            )

            self.assertEqual(step_completed.returncode, 0, step_completed.stderr + step_completed.stdout)
            hook = json.loads(step_completed.stdout)
            self.assertEqual(hook["decision"], "block")
            self.assertIn("agent-harness choose", hook["reason"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "choosing")
            self.assertEqual(state["choosing"], ["information_completeness_review"])

            options_completed = self.run_cli("options", "--state", str(state_path), "--json")
            self.assertEqual(options_completed.returncode, 0, options_completed.stderr + options_completed.stdout)
            options = json.loads(options_completed.stdout)["choice"]["options"]
            self.assertEqual([option["id"] for option in options], ["collect_more_sources", "start_data_processing"])

            choose_completed = self.run_cli(
                "choose",
                "start_data_processing",
                "--state",
                str(state_path),
                "--reason",
                "The collected sources are sufficient.",
                "--json",
            )

            self.assertEqual(choose_completed.returncode, 0, choose_completed.stderr + choose_completed.stdout)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["active"], ["data_processing"])
            self.assertEqual(state["completed"], ["information_completeness_review"])

    def test_workflow_human_approval_waits_then_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, report_dir, _ = self.make_temp_project(Path(temp_dir))
            workflow_path = Path(temp_dir) / "workflows" / "approval.json"
            state_path = Path(temp_dir) / ".agent-harness" / "state.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "approval_flow",
                    "version": 1,
                    "initial": "scope_approval",
                    "nodes": [
                        {
                            "id": "scope_approval",
                            "type": "human_approval",
                            "title": "Scope approval",
                            "transitions": [
                                {
                                    "id": "approve_scope",
                                    "when": "user.approved",
                                    "action": "advance",
                                    "to": "data_processing",
                                },
                                {
                                    "id": "reject_scope",
                                    "when": "user.rejected",
                                    "action": "fail",
                                },
                            ],
                        },
                        {"id": "data_processing", "type": "terminal"},
                    ],
                },
            )

            waiting_completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-waiting",
                "--hook-json",
            )

            self.assertEqual(waiting_completed.returncode, 0, waiting_completed.stderr + waiting_completed.stdout)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "waiting")
            self.assertEqual(state["waiting"], ["scope_approval"])

            approve_completed = self.run_cli(
                "approve",
                "scope_approval",
                "--state",
                str(state_path),
                "--reason",
                "Scope accepted.",
                "--json",
            )
            self.assertEqual(approve_completed.returncode, 0, approve_completed.stderr + approve_completed.stdout)

            advanced_completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-approved",
                "--hook-json",
            )

            self.assertEqual(advanced_completed.returncode, 0, advanced_completed.stderr + advanced_completed.stdout)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["active"], ["data_processing"])
            self.assertEqual(state["completed"], ["scope_approval"])

    def test_workflow_terminal_completion_does_not_block_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, report_dir, _ = self.make_temp_project(Path(temp_dir))
            workflow_path = Path(temp_dir) / "workflows" / "done.json"
            state_path = Path(temp_dir) / ".agent-harness" / "state.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "done_flow",
                    "version": 1,
                    "initial": "final_review",
                    "nodes": [{"id": "final_review", "type": "terminal"}],
                },
            )

            completed = self.run_cli(
                "step",
                "--task",
                str(workflow_path),
                "--state",
                str(state_path),
                "--report-dir",
                str(report_dir),
                "--report-id",
                "workflow-done",
                "--hook-json",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            hook = json.loads(completed.stdout)
            self.assertEqual(hook["systemMessage"], "Agent harness workflow completed.")
            self.assertNotIn("decision", hook)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["active"], [])
            self.assertEqual(state["completed"], ["final_review"])

    def test_workflow_validate_allows_failed_as_status_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "workflows" / "status_value.json"
            self.write_workflow(
                workflow_path,
                {
                    "id": "status_value_flow",
                    "version": 1,
                    "initial": "analysis",
                    "nodes": [
                        {
                            "id": "analysis",
                            "type": "stage",
                            "checks": [],
                            "transitions": [
                                {
                                    "id": "redesign_after_failed_status",
                                    "when": "checks.analysis_status.metadata.status == \"failed\"",
                                    "action": "advance",
                                    "to": "research_design",
                                }
                            ],
                        },
                        {"id": "research_design", "type": "terminal"},
                    ],
                },
            )

            completed = self.run_cli(
                "validate-workflow",
                "--task",
                str(workflow_path),
                "--json",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["valid"])
            self.assertEqual(payload["errors"], [])


if __name__ == "__main__":
    unittest.main()

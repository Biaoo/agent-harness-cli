from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_harness_cli.config import load_json, project_root_from_task
from agent_harness_cli.reporting import build_report, make_report_id, summarize_results, write_report
from agent_harness_cli.types import failure_result, normalize_check_result


def command_for_check(command: list[str], input_path: Path) -> list[str]:
    values = [
        part.replace("{input}", str(input_path)).replace("{python}", sys.executable)
        for part in command
    ]
    if any(str(input_path) in part for part in values):
        return values
    return values + ["--input", str(input_path)]


def run_check_command(
    *,
    root: Path,
    task_path: Path,
    task: dict[str, Any],
    check_config: dict[str, Any],
    input_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    command = check_config.get("command")
    name = str(check_config.get("name", "unnamed_check"))
    severity = str(check_config.get("severity", "error"))
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        return failure_result(
            check=name,
            severity=severity,
            summary="Check command is not configured correctly.",
            message="Each check must define command as a list of strings.",
            suggestion='Update the task check entry, for example: ["{python}", "checks/my_check.py"].',
            evidence={"command": command},
        )

    input_path = input_dir / f"{name}.input.json"
    input_payload = {
        "root": str(root),
        "task_path": str(task_path),
        "task": task,
        "check": check_config,
    }
    input_path.write_text(json.dumps(input_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    resolved_command = command_for_check(command, input_path)
    try:
        completed = subprocess.run(
            resolved_command,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return failure_result(
            check=name,
            severity=severity,
            summary="Check command timed out.",
            message=f"Command exceeded timeout of {timeout_seconds:g} seconds.",
            suggestion="Make the check deterministic and bounded, or raise the timeout intentionally.",
            evidence={
                "command": resolved_command,
                "timeout_seconds": timeout_seconds,
                "stdout": exc.stdout,
                "stderr": exc.stderr,
            },
        )
    except OSError as exc:
        return failure_result(
            check=name,
            severity=severity,
            summary="Check command could not be started.",
            message=f"{type(exc).__name__}: {exc}",
            suggestion="Verify the command executable and working directory.",
            evidence={"command": resolved_command},
        )

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return failure_result(
            check=name,
            severity=severity,
            summary="Check command did not return valid JSON on stdout.",
            message=f"JSON parse failed: {exc}",
            suggestion="Write only the check-result JSON to stdout; send logs to stderr.",
            evidence={
                "command": resolved_command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            },
        )

    if not isinstance(parsed, dict):
        return failure_result(
            check=name,
            severity=severity,
            summary="Check command returned JSON, but not an object.",
            message="The check-result contract requires a JSON object.",
            suggestion="Return an object with check, passed, severity, summary, and reasons.",
            evidence={"command": resolved_command, "exit_code": completed.returncode},
        )

    result = normalize_check_result(parsed, check_config)
    metadata = result.setdefault("metadata", {})
    metadata["command"] = resolved_command
    metadata["exit_code"] = completed.returncode
    if completed.stderr.strip():
        metadata["stderr_tail"] = completed.stderr[-2000:]
    return result


def run_configured_checks(
    *,
    root: Path,
    task_path: Path,
    task: dict[str, Any],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    checks = task.get("checks", [])
    if not isinstance(checks, list):
        raise SystemExit("Task checks must be a list.")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agent-harness-") as temp_dir:
        input_dir = Path(temp_dir)
        for check_config in checks:
            if not isinstance(check_config, dict):
                results.append(
                    failure_result(
                        check="invalid_check",
                        severity="error",
                        summary="Task contains an invalid check entry.",
                        message="Each checks item must be an object.",
                        suggestion="Replace invalid check entries with objects containing name and command.",
                    )
                )
                continue
            results.append(
                run_check_command(
                    root=root,
                    task_path=task_path,
                    task=task,
                    check_config=check_config,
                    input_dir=input_dir,
                    timeout_seconds=timeout_seconds,
                )
            )
    return results


def format_summary(report: dict[str, Any], report_path: Path, report_dir: str) -> str:
    summary = report["summary"]
    if not report["passed"]:
        status = f"FAILED {summary['failed_checks']}/{summary['total_checks']} checks"
    elif summary["warning_failures"]:
        status = f"PASSED with {summary['warning_failures']} warning check(s)"
    else:
        status = f"PASSED {summary['total_checks']}/{summary['total_checks']} checks"
    view_command = f"agent-harness view {report['report_id']}"
    failed_command = f"agent-harness view {report['report_id']} --failed-only"
    if report_dir != "reports":
        view_command += f" --report-dir {report_dir}"
        failed_command += f" --report-dir {report_dir}"
    return "\n".join(
        [
            status,
            f"report_id: {report['report_id']}",
            f"report_path: {report_path}",
            "",
            "Next:",
            f"  {view_command}",
            f"  {failed_command}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run external check scripts for an agentic task.")
    parser.add_argument("--task", required=True, help="Path to a task JSON file.")
    parser.add_argument("--report-dir", default="reports", help="Directory for generated report files.")
    parser.add_argument("--report-id", help="Optional stable report id. Defaults to timestamp-task.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Timeout per check command in seconds.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary JSON.")
    args = parser.parse_args(argv)

    task_path = Path(args.task).resolve()
    root = project_root_from_task(task_path)
    task = load_json(task_path)
    checks = task.get("checks", [])
    if not checks:
        raise SystemExit("Task has no checks configured.")

    results = run_configured_checks(
        root=root,
        task_path=task_path,
        task=task,
        timeout_seconds=args.timeout,
    )
    report_id = args.report_id or make_report_id(str(task.get("id", "unknown_task")))
    report = build_report(task_path=task_path, task=task, results=results, report_id=report_id)
    report_path = write_report(root / args.report_dir, report_id, report)
    if args.json:
        print(
            json.dumps(
                {"report_id": report_id, "report_path": str(report_path), **summarize_results(results)},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(format_summary(report, report_path, args.report_dir))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness_cli.config import load_json, write_json


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "task"


def make_report_id(task_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{slugify(task_id)}"


def resolve_report_path(report_ref: str, report_dir: str | Path) -> Path:
    candidate = Path(report_ref)
    if candidate.exists():
        return candidate.resolve()
    if candidate.suffix == ".json":
        return (Path(report_dir) / candidate.name).resolve()
    return (Path(report_dir) / f"{report_ref}.json").resolve()


def summarize_results(results: list[dict[str, Any]]) -> dict[str, int]:
    failed = [result for result in results if not result.get("passed", False)]
    blocking = [result for result in failed if str(result.get("severity", "error")) == "error"]
    warnings = [result for result in failed if str(result.get("severity", "error")) == "warning"]
    return {
        "total_checks": len(results),
        "failed_checks": len(failed),
        "blocking_failures": len(blocking),
        "warning_failures": len(warnings),
    }


def next_actions(results: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for result in results:
        if result.get("passed", False):
            continue
        for reason in result.get("reasons", []):
            suggestion = reason.get("suggestion") if isinstance(reason, dict) else None
            if suggestion:
                actions.append(str(suggestion))
    return actions


def build_report(
    *,
    task_path: Path,
    task: dict[str, Any],
    results: list[dict[str, Any]],
    report_id: str,
) -> dict[str, Any]:
    summary = summarize_results(results)
    return {
        "report_id": report_id,
        "task_id": task.get("id", "unknown_task"),
        "task_path": str(task_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "passed": summary["blocking_failures"] == 0,
        "summary": summary,
        "checks": results,
        "next_actions": next_actions(results),
    }


def write_report(report_dir: str | Path, report_id: str, report: dict[str, Any]) -> Path:
    path = Path(report_dir) / f"{report_id}.json"
    write_json(path, report)
    return path.resolve()


def load_report(report_ref: str, report_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    path = resolve_report_path(report_ref, report_dir)
    return path, load_json(path)

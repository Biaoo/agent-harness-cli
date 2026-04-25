from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from agent_harness_cli.reporting import load_report


def flatten_items(report: dict[str, Any], *, failed_only: bool, check_name: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for check in report.get("checks", []):
        if check_name and check.get("check") != check_name:
            continue
        if failed_only and check.get("passed", False):
            continue
        reasons = check.get("reasons", [])
        if reasons:
            for reason in reasons:
                items.append({"kind": "reason", "check": check, "reason": reason})
        else:
            items.append({"kind": "check", "check": check, "reason": None})
    return items


def page_items(items: list[dict[str, Any]], page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    total_pages = max(1, math.ceil(len(items) / page_size))
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start : start + page_size], total_pages


def format_item(index: int, item: dict[str, Any]) -> str:
    check = item["check"]
    status = "PASS" if check.get("passed", False) else "FAIL"
    score = check.get("score")
    score_text = f" score={score}" if score is not None else ""
    lines = [
        f"[{index}] {check.get('check')} {status} {check.get('severity', 'error')}{score_text}",
        f"    {check.get('summary', '')}",
    ]
    reason = item.get("reason")
    if reason:
        if reason.get("file"):
            location = f"    file: {reason['file']}"
            if reason.get("line"):
                location += f":{reason['line']}"
            lines.append(location)
        lines.append(f"    message: {reason.get('message', '')}")
        if reason.get("suggestion"):
            lines.append(f"    suggestion: {reason['suggestion']}")
        if reason.get("requires_user_input"):
            lines.append("    requires_user_input: true")
        if reason.get("evidence"):
            lines.append(f"    evidence: {json.dumps(reason['evidence'], ensure_ascii=False)}")
    return "\n".join(lines)


def format_page(
    *,
    report_ref: str,
    report_path: Path,
    report: dict[str, Any],
    items: list[dict[str, Any]],
    page: int,
    page_size: int,
    total_pages: int,
    total_items: int,
    failed_only: bool,
    check_name: str | None,
    report_dir: str,
) -> str:
    summary = report.get("summary", {})
    start_index = (page - 1) * page_size + 1 if total_items else 0
    end_index = start_index + len(items) - 1 if items else 0
    lines = [
        f"report_id: {report.get('report_id', report_ref)}",
        f"report_path: {report_path}",
        f"task_id: {report.get('task_id', 'unknown_task')}",
        f"status: {'PASSED' if report.get('passed', False) else 'FAILED'}",
        "checks: "
        f"total={summary.get('total_checks', 0)} "
        f"failed={summary.get('failed_checks', 0)} "
        f"blocking={summary.get('blocking_failures', 0)}",
        f"page: {page}/{total_pages} items {start_index}-{end_index} of {total_items}",
        "",
    ]
    for offset, item in enumerate(items):
        lines.append(format_item(start_index + offset, item))
        lines.append("")
    if page < total_pages:
        command = f"agent-harness view {report_ref} --page {page + 1} --page-size {page_size}"
        if report_dir != "reports":
            command += f" --report-dir {report_dir}"
        if failed_only:
            command += " --failed-only"
        if check_name:
            command += f" --check {check_name}"
        lines.append(f"next: {command}")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="View an agent-harness report one page at a time.")
    parser.add_argument("report", help="Report id or path.")
    parser.add_argument("--report-dir", default="reports", help="Directory containing report JSON files.")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=5)
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--check", help="Show only one check name.")
    parser.add_argument("--json", action="store_true", help="Print the selected page as JSON.")
    args = parser.parse_args(argv)

    report_path, report = load_report(args.report, args.report_dir)
    page_size = max(1, args.page_size)
    all_items = flatten_items(report, failed_only=args.failed_only, check_name=args.check)
    selected, total_pages = page_items(all_items, max(1, args.page), page_size)
    page_number = max(1, min(args.page, total_pages))

    if args.json:
        print(
            json.dumps(
                {
                    "report_id": report.get("report_id", args.report),
                    "report_path": str(report_path),
                    "page": page_number,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "total_items": len(all_items),
                    "items": selected,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(
            format_page(
                report_ref=args.report,
                report_path=report_path,
                report=report,
                items=selected,
                page=page_number,
                page_size=page_size,
                total_pages=total_pages,
                total_items=len(all_items),
                failed_only=args.failed_only,
                check_name=args.check,
                report_dir=args.report_dir,
            ),
            end="",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

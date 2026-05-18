from __future__ import annotations

import argparse
import sys

from agent_harness_cli.runners.run_checks import main as run_checks_main
from agent_harness_cli.runners.view import main as view_main
from agent_harness_cli.runners.workflow import (
    WORKFLOW_COMMAND_DESCRIPTIONS,
    WORKFLOW_COMMANDS,
    main as workflow_main,
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in WORKFLOW_COMMANDS:
        return workflow_main(argv)

    parser = argparse.ArgumentParser(prog="agent-harness")
    subparsers = parser.add_subparsers(dest="command")

    run_checks_parser = subparsers.add_parser("run-checks", help="Run configured task checks and write a report.")
    run_checks_parser.add_argument("--task", required=True)
    run_checks_parser.add_argument("--report-dir", default="reports")
    run_checks_parser.add_argument("--report-id")
    run_checks_parser.add_argument("--timeout", type=float, default=120.0)
    run_checks_parser.add_argument("--json", action="store_true")

    view_parser = subparsers.add_parser("view", help="View a generated report page by page.")
    view_parser.add_argument("report")
    view_parser.add_argument("--report-dir", default="reports")
    view_parser.add_argument("--page", type=int, default=1)
    view_parser.add_argument("--page-size", type=int, default=5)
    view_parser.add_argument("--failed-only", action="store_true")
    view_parser.add_argument("--check")
    view_parser.add_argument("--json", action="store_true")

    for command in sorted(WORKFLOW_COMMANDS):
        subparsers.add_parser(
            command,
            help=WORKFLOW_COMMAND_DESCRIPTIONS.get(command),
            add_help=False,
        )

    args = parser.parse_args(argv)
    if args.command == "run-checks":
        forwarded = [
            "--task",
            args.task,
            "--report-dir",
            args.report_dir,
            "--timeout",
            str(args.timeout),
        ]
        if args.report_id:
            forwarded += ["--report-id", args.report_id]
        if args.json:
            forwarded.append("--json")
        return run_checks_main(forwarded)
    if args.command == "view":
        forwarded = [
            args.report,
            "--report-dir",
            args.report_dir,
            "--page",
            str(args.page),
            "--page-size",
            str(args.page_size),
        ]
        if args.failed_only:
            forwarded.append("--failed-only")
        if args.check:
            forwarded += ["--check", args.check]
        if args.json:
            forwarded.append("--json")
        return view_main(forwarded)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

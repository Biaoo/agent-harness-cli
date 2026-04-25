# Agent Harness CLI

`agent-harness-cli` is a thin, dependency-free CLI for agentic task checks. It does
not own domain logic. It runs user-defined check scripts, stores report JSON,
and lets agents page through reports without dumping everything at once.

Install:

```bash
uv tool install agent-harness-cli
```

The core shape is:

```text
Task spec + external check commands + report store + paginated viewer
```

## Quick Start

Run checks for a task file from any workspace:

```bash
agent-harness run-checks --task task.json --report-id sample-report
```

The command prints a compact summary:

```text
PASSED 2/2 checks
report_id: sample-report
report_path: reports/sample-report.json

Next:
  agent-harness view sample-report
  agent-harness view sample-report --failed-only
```

View a report one page at a time:

```bash
agent-harness view sample-report --page 1 --page-size 5
agent-harness view sample-report --failed-only
```

Run tests:

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

Build package distributions:

```bash
uv build
```

## Project Layout

```text
src/agent_harness_cli/
  runners/       Thin CLI implementations for run-checks and view.
skills/          Skill that teaches agents how to design check scripts.
schemas/         JSON schemas for tasks, check results, and reports.
tests/           Self-contained CLI tests.
```

## Check Command Contract

Each task check declares a command:

```json
{
  "name": "todo_markers",
  "command": ["{python}", "checks/todo_markers.py"],
  "severity": "warning",
  "config": {
    "patterns": ["TODO", "TBD"]
  }
}
```

The harness writes an input JSON file and appends `--input <path>` unless the
command already contains `{input}`. It also replaces `{python}` with the current
Python interpreter.

The input contains:

```json
{
  "root": "project root provided by harness",
  "task_path": "task.json",
  "task": {},
  "check": {}
}
```

## Check Result Contract

Every check returns this shape:

```json
{
  "check": "required_artifacts",
  "passed": true,
  "score": 1.0,
  "severity": "error",
  "summary": "All required artifacts exist.",
  "reasons": []
}
```

Failed checks should include specific reasons with evidence and a suggested fix.

## Design Notes

- The PyPI distribution is `agent-harness-cli`; the installed command is `agent-harness`.
- Deterministic checks should be preferred over LLM judges.
- LLM judge checks can import `agent_harness_cli.llm.codex_judge`, which calls local `codex exec` and supports checklist-based judging.
- Warnings guide an agent without blocking the run.
- Error-level failures block the run.
- Domain logic belongs in user-owned check scripts.
- Use `skills/harness-check-designer/SKILL.md` when asking an agent to design a new check.
- JSON is used for task and report files to avoid parser dependencies.

## Publishing

The GitHub workflow at `.github/workflows/publish.yml` publishes on tags that
match `v*.*.*`. The tag version must match `[project].version` without the
leading `v`.

```bash
git tag v0.1.0
git push origin v0.1.0
```

Publishing uses PyPI Trusted Publishing with the `pypi` GitHub environment.
Configure the PyPI project `agent-harness-cli` to trust this repository and the
workflow file `.github/workflows/publish.yml` before pushing a release tag.

---
name: harness-check-designer
description: Use when designing or implementing Agent Harness CLI checks, Stop hooks, workflow-controller specs, or project AGENTS.md instructions that turn ambiguous agentic task criteria into deterministic checks, workflow graphs, state transitions, and Codex continuation loops.
---

# Harness Check Designer

Use this skill to design checks for `agent-harness`. The harness only runs check
commands and stores reports; each check script owns its domain logic.

## Positioning

Agent Harness Engineering means engineering the system around an AI agent so the
agent's work is constrained, observable, verifiable, and improved through
executable feedback loops.

`agent-harness` is a focused reference framework, not an eval platform, agent
runtime, or built-in rule library. This skill is for designing artifact
acceptance loops and workflow-controller projects: define artifact contracts,
encode project-owned checks, write agent-readable reports, design workflow
state routing, and wire blocked checks back into the next agent pass.

## Workflow

1. Convert the user's quality requirement into one narrow check.
2. Prefer deterministic checks: schema, parser, filesystem, trace, AST, grep, or golden comparison.
3. Use an LLM judge only when code cannot reasonably decide the requirement.
4. Define the check before writing code:

```md
name:
purpose:
input:
non_goals:
pass_rule:
fail_rule:
severity:
reason_fields:
edge_cases:
example_pass:
example_fail:
command:
```

5. Implement the check as a standalone command. It must accept `--input <path>`
and print exactly one JSON object to stdout. Send logs to stderr.
6. The check should be read-only by default. If it needs temporary files, write
outside the user's artifacts or to a temp directory.
7. Add at least one passing and one failing fixture when the check is non-trivial.

## Workflow Controller Projects

Use workflow-controller mode when a task has multiple validated stages, gates,
repairs, user approvals, or model-choice routing. Design the project so users
can start with a normal task statement instead of a long harness prompt:

- `workflows/<name>.json` owns graph structure, node checks, transitions, and
  model-choice policies.
- Project `AGENTS.md` tells Codex how to start from a plain user task or idea.
- Node artifacts and allowed statuses live in project docs near the workflow.
- Stop hooks call `agent-harness step --task <workflow> --hook-json`.
- Runtime state and reports are generated files and should be ignored.

For workflow checks, keep scripts deterministic and narrow. A common pattern is:

- a structure check that verifies an artifact exists and has required sections;
- a status check that parses `Status: <value>` and returns it in
  `metadata.status`;
- transition conditions that route on `checks.<name>.metadata.status`.

Model choice should be controlled by workflow transitions, not by open-ended
natural language. Put clear labels and prompts on candidate transitions so a
runner can inspect `agent-harness options` and choose with an auditable reason.

## Project AGENTS.md For Workflow Examples

When creating an example workflow project, add a concise `AGENTS.md` so a user
can start with a normal task statement:

```text
Research this idea: <idea>
```

The `AGENTS.md` should define:

- workflow spec path;
- state path and report directory;
- artifact contract location;
- how to determine the active node;
- how to create/update the active artifact;
- the required routing marker, for example `Status: <allowed_status>`;
- how `choosing`, `waiting`, and blocked hook feedback should be handled;
- any domain-specific hard rule, such as "do not downgrade the research target."

Keep this file project-specific. Keep the skill generic.

## Input Contract

The harness passes a JSON object with:

```json
{
  "root": "project root provided by harness",
  "task_path": "task.json",
  "task": {},
  "check": {}
}
```

Resolve relative artifact, trace, and fixture paths against `root`.

Workflow-controller checks receive the same fields plus workflow context:

```json
{
  "root": "project root provided by harness",
  "task_path": "workflows/example.json",
  "task": {},
  "check": {},
  "workflow": {},
  "state": {},
  "node": {},
  "artifacts": {}
}
```

Do not require all workflow fields for simple checks. Use them only when the
check genuinely depends on workflow state or the active node.

## Output Contract

Each check must return:

```json
{
  "check": "todo_markers",
  "passed": false,
  "severity": "warning",
  "summary": "Found 2 unresolved marker occurrence(s).",
  "score": 0.0,
  "reasons": [
    {
      "file": "03_drafts/proposal.md",
      "line": 42,
      "message": "Found unresolved marker: TODO",
      "suggestion": "Resolve the marker or move it into an explicit review note.",
      "requires_user_input": false,
      "evidence": { "line_excerpt": "TODO: add budget source" }
    }
  ]
}
```

Required fields: `check`, `passed`, `severity`, `summary`, `reasons`.

## Check Design Rules

- One check, one concern.
- Reasons must say where the issue is, why it failed, and how to fix it.
- Use `error` only for issues that should block handoff.
- Use `warning` for issues an agent can surface without blocking.
- If the fix needs human judgment, set `requires_user_input: true`.
- Do not hide failures in logs; make them structured reasons.
- Do not inspect unrelated files unless the check spec says so.

## Codex Stop Hooks For `run-checks`

When wiring `agent-harness run-checks` into a Codex `Stop` hook, translate the
harness result into Codex hook semantics:

- Keep `agent-harness run-checks` exit code semantics unchanged: `0` means no
  blocking failures; `1` means at least one blocking check failed.
- A failed harness run is usually not a hook crash. Capture the status, read the
  report, and emit a Codex continuation decision.
- For blocking failures, print JSON with `decision: "block"` and an actionable
  `reason`. Codex treats this as "continue the turn with this reason".
- For success, exit `0` with no output.
- Do not rely on `additionalContext` for blocking failures; it informs Codex but
  does not force another pass.
- Keep the continuation reason short: report path, paginated view command, failed
  check names, summaries, and suggestions.
- Resolve the hook script from the git root in `.codex/hooks.json`; Codex can be
  started from a subdirectory.
- If any check calls local `codex exec`, add a recursion guard such as
  `AGENT_HARNESS_HOOK_ACTIVE=1`.

Minimal Stop hook pattern:

```bash
set +e
agent-harness run-checks --task task.json --report-id latest > reports/latest.summary.txt 2>&1
status=$?
set -e

if [ "$status" -eq 0 ]; then
  exit 0
fi

python3 - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("reports/latest.json").read_text(encoding="utf-8"))
failed = [c for c in report["checks"] if not c.get("passed")]
lines = []
for check in failed:
    lines.append(f"- {check['check']}: {check['summary']}")
    for reason in check.get("reasons", [])[:2]:
        if reason.get("suggestion"):
            lines.append(f"  Suggestion: {reason['suggestion']}")

print(json.dumps({
    "decision": "block",
    "reason": "\n".join([
        "Agent harness found blocking failures. Update the artifact and rerun the harness.",
        "Report: reports/latest.json",
        "View failed checks: agent-harness view latest --failed-only --page-size 5",
        "",
        "\n".join(lines),
    ]),
}, ensure_ascii=False))
PY
```

## Codex Stop Hooks For `step`

When designing workflow-controller projects, keep hook logic thin. The CLI should
own state updates, transition evaluation, report writing, and hook JSON:

```bash
agent-harness step \
  --task workflows/example.json \
  --report-id latest \
  --hook-json
```

If the project needs to use an unreleased sibling CLI checkout, set
`PYTHONPATH=../agent-harness-cli/src` in the hook before invoking
`python3 -m agent_harness_cli.cli`.

The hook should pass through valid hook JSON from `step`. If `step` fails before
producing JSON, emit a `decision: "block"` payload with stdout/stderr tails so
Codex can diagnose the project setup.

## LLM Judge Checks

Use an LLM judge only after deterministic checks. Prefer checklist-based LLM
checks over strict JSON generation: Codex fills a Markdown checklist, then the
check script parses that checklist into harness JSON.

Core principle:

```text
Codex should not produce the final harness JSON.
Codex should fill a review artifact.
The check script should parse that artifact into harness JSON.
```

This lowers workflow fragility. Markdown checklists are easier for models to
complete consistently and easier for humans to audit. Harness JSON remains the
machine-facing contract, but it is produced by code, not by the model.

Rules:

- Build a small evidence bundle; do not ask Codex to browse the workspace.
- Give a checklist with concrete yes/no items.
- Put LLM provider calls in the user's check script or workspace, not in the harness CLI.
- Keep Codex read-only when using local `codex exec`, with an ephemeral session when possible.
- Parse `- [x]` and `- [ ]` deterministically; do not trust prose as the source of truth.
- Return parsed checklist results inside the normal check-result contract.
- Default LLM content checks to `warning` unless the rubric is stable enough to block.

Checklist contract:

```md
# Checklist Name

- [ ] item_id: thesis_relevant
  Criterion: The central claim directly addresses the requested topic.
  Evidence:
  Reason:
  Suggestion:

- [ ] item_id: counterargument
  Criterion: The essay responds to a plausible opposing concern.
  Evidence:
  Reason:
  Suggestion:
```

Parser rules:

- Treat checked items as pass and unchecked items as fail.
- Prefer stable `item_id` values over item text when creating reasons.
- If the filled checklist has no parseable checkbox items, fail closed.
- Preserve the filled checklist in `metadata` when useful for audit.
- Do not make free-form prose the source of truth for pass/fail.

## Bad Checks

Avoid:

- Broad checks such as "judge overall quality".
- Checks that depend on live network state without explicit config.
- Checks that mutate user artifacts.
- Checks that fail without evidence.
- LLM judges without a rubric.
- Scripts that print prose before or after the JSON result.

## Template

Copy `assets/check_template.py` when creating a new Python check.

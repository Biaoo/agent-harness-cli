# Agent Harness CLI

[English](README.md) | [简体中文](README.zh-CN.md)

A practical reference framework for building acceptance loops around AI agent work.

`agent-harness-cli` helps you turn "the agent seems done" into executable,
inspectable feedback. The CLI runs project-owned check scripts, writes
machine-readable reports, gives Codex a paginated report surface it can use for
the next pass, and can control long-running workflows through explicit state.

## What Is Agent Harness Engineering?

Agent Harness Engineering is the practice of engineering the system around an AI
agent so the agent's work becomes constrained, observable, verifiable, and
iteratively improvable through executable feedback loops.

In broader use, that system can include task specs, tools, runtime context,
memory, safety controls, environments, evaluators, reports, and human handoff.
This project is a focused reference framework for one practical surface of that
idea: artifact acceptance loops for Codex workspaces.

Instead of relying on an agent's final message, you define:

- the artifact the agent must produce
- the checks that decide whether the artifact is acceptable
- the report format the agent can inspect
- the hook behavior that turns failures into the next agent prompt

`agent-harness-cli` provides the smallest reusable skeleton for that loop. Your
workspace owns the domain rules: checks, rubrics, fixtures, and optional LLM
judge calls.

## Why It Matters

Agentic work often fails at the last mile: the artifact exists, but nobody has
turned the acceptance criteria into a feedback loop the agent can actually use.

`agent-harness-cli` gives you a reference implementation of a project-local
acceptance loop:

- deterministic checks for objective requirements
- checklist or LLM-assisted checks for semantic requirements
- warning checks for guidance and error checks for blocking failures
- JSON reports for reproducibility and audit
- paginated report viewing for large outputs
- Codex Stop hooks that turn failed checks into continuation prompts
- workflow state control for multi-stage agentic tasks

The key idea is simple: **a failed check should become useful context for the
next agent pass.**

## Good Fit

Use this when an agent produces an artifact that can be checked, and you want a
pattern your project can copy and adapt:

- project documents, proposals, specifications, and research notes
- code changes that need repository-specific acceptance checks
- generated data reports or analysis outputs
- writing tasks with strict length, structure, or quality requirements
- long-running agentic workflows where the agent should iterate after failures

The main design boundary is that domain logic stays in the workspace's own check
scripts, while the CLI provides a consistent way to run those checks and inspect
their results.

## What This Is Not

`agent-harness-cli` is not trying to be a complete agent platform. It is not:

- an eval platform
- an agent runtime
- a general workflow engine
- a large built-in check library
- a replacement for project tests, linting, review, or domain judgment

The value is the reference pattern: define the artifact, run project-owned
checks, write a report, and feed failures back into the next agent pass.

## Related Projects

- Runnable example: [Biaoo/agent-harness-cli-example](https://github.com/Biaoo/agent-harness-cli-example)
- This repository contains the CLI and two Codex skills:
  `harness-check-designer` for designing harnesses and
  `harness-workflow-runner` for operating existing workflow projects.
  Both skills are domain-neutral; project repositories provide their own
  workflow-specific instructions, checks, and optional project-level skills.

## Install

Install the CLI with `uv`:

```bash
uv tool install agent-harness-cli
```

Or run the published package without a global install:

```bash
uvx --from agent-harness-cli agent-harness --help
```

## Install Codex Skills

Install the designer skill when you want Codex to create or modify checks,
workflow specs, hooks, or project `AGENTS.md` files:

```bash
npx skills add Biaoo/agent-harness-cli --skill harness-check-designer -a codex
```

Install the runner skill when you want Codex to operate an existing workflow
project from a short user task or idea:

```bash
npx skills add Biaoo/agent-harness-cli --skill harness-workflow-runner -a codex
```

For a global Codex skill install, add `-g`:

```bash
npx skills add Biaoo/agent-harness-cli --skill harness-check-designer -a codex -g
npx skills add Biaoo/agent-harness-cli --skill harness-workflow-runner -a codex -g
```

Use `$harness-check-designer` when designing the harness. Use
`$harness-workflow-runner` when running an existing workflow.

## Build an Acceptance Loop

In normal use, configure the project so Codex runs the harness automatically
when a turn stops. Manual CLI commands are mainly useful while developing or
debugging checks.

1. **Write the artifact contract.** Define what Codex should produce and where it
   should save it.
2. **Encode acceptance checks.** Write focused scripts under your project, such
   as `checks/check_required_sections.py`.
3. **Describe the task.** Put the artifact and checks in `task.json`.
4. **Wire the Stop hook.** Run `agent-harness run-checks` when Codex attempts to
   finish.
5. **Continue on failure.** Convert blocking failures into a Codex continuation
   decision so the agent keeps working.
6. **Inspect the report.** Use `agent-harness view` while debugging or when an
   agent needs paginated report context.

Minimal `task.json`:

```json
{
  "id": "proposal_review",
  "artifacts": [
    {
      "name": "proposal",
      "path": "proposal.md",
      "type": "markdown",
      "required": true
    }
  ],
  "checks": [
    {
      "name": "required_sections",
      "command": ["{python}", "checks/check_required_sections.py"],
      "severity": "error",
      "config": {
        "artifact": "proposal"
      }
    }
  ]
}
```

Minimal `.codex/hooks.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$(git rev-parse --show-toplevel)/.codex/hooks/run-agent-harness-check.sh\"",
            "async": false,
            "timeout": 360,
            "statusMessage": "Running agent harness"
          }
        ]
      }
    ]
  }
}
```

See the runnable example for a complete Stop hook script:
[Biaoo/agent-harness-cli-example](https://github.com/Biaoo/agent-harness-cli-example).

## Codex Stop Hooks

`agent-harness run-checks` exits with `1` when blocking checks fail. That is
correct CLI behavior. In a Codex Stop hook, translate that result into a Codex
continuation decision:

```json
{
  "decision": "block",
  "reason": "Agent harness found blocking failures. Update the artifact and rerun the harness."
}
```

For Stop hooks, this does not reject the turn. It tells Codex to continue with
the reason as the next prompt. Use the generated report to give Codex a short,
actionable continuation reason.

## Manual Debugging

Use the CLI directly when developing or debugging checks:

```bash
agent-harness run-checks --task task.json --report-id sample-report
```

The command writes a report and prints a compact summary:

```text
PASSED 2/2 checks
report_id: sample-report
report_path: reports/sample-report.json

Next:
  agent-harness view sample-report
  agent-harness view sample-report --failed-only
```

View the report one page at a time:

```bash
agent-harness view sample-report --page 1 --page-size 5
agent-harness view sample-report --failed-only
```

## Workflow Controller

For long-running tasks, define a workflow graph instead of a single final
`task.json`. The workflow controller validates the current active node, updates
state, writes reports, and returns hook JSON that keeps Codex working until the
workflow completes.

Common commands:

```bash
agent-harness validate-workflow --task workflows/<workflow>.json
agent-harness step --task workflows/<workflow>.json --hook-json
agent-harness status --state .agent-harness/state.json
agent-harness history --state .agent-harness/state.json
agent-harness options --state .agent-harness/state.json
agent-harness choose <transition-id> --state .agent-harness/state.json --reason "why this route is correct"
agent-harness approve <node-id> --state .agent-harness/state.json --reason "user approved"
agent-harness reject <node-id> --state .agent-harness/state.json --reason "user rejected"
```

Workflow node checks receive the normal check input plus `workflow`, `state`,
`node`, and `artifacts` context. See the runnable example and the full design
document: [Workflow Controller Design](docs/workflow-controller-design.zh-CN.md).

## How It Works

The core shape is:

```text
task.json + external check commands + report store + paginated viewer
```

Each check is an ordinary command. The harness writes an input JSON file and
appends `--input <path>` unless the command already contains `{input}`. It also
replaces `{python}` with the current Python interpreter.

Task check example:

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

Input passed to each check:

```json
{
  "root": "project root provided by harness",
  "task_path": "task.json",
  "task": {},
  "check": {}
}
```

Check result shape:

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

## Design Principles

- Keep the CLI thin and the workspace in control.
- Deterministic checks should be preferred over LLM judges.
- LLM judge checks should own model-call logic inside the user's check script or
  workspace.
- Warning failures guide an agent without blocking the run.
- Error failures block handoff.
- Domain logic belongs in user-owned check scripts.
- JSON is used for task and report files to avoid parser dependencies.

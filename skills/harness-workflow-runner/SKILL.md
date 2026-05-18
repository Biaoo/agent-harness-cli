---
name: harness-workflow-runner
description: Use when operating an existing Agent Harness CLI workflow-controller project: start from a user task or idea, read project AGENTS.md/workflow state, produce active-node artifacts, handle step/options/choose/approve/reject, and continue until the workflow completes.
---

# Harness Workflow Runner

Use this skill to run an existing `agent-harness` workflow-controller project.
Do not redesign the workflow, checks, or hooks unless the user explicitly asks.

## Start From The Project

When the user gives a plain task or idea, first read the project instructions:

- `AGENTS.md`
- workflow spec referenced there, usually `workflows/*.json`
- artifact contract docs referenced there

If state exists, continue from the current active node. If no state exists, use
the workflow's initial node.

## Operating Loop

1. Inspect current state with `agent-harness status --state <state-path>` when a
   state file exists.
2. Read the active node's artifact contract.
3. Create or update only the artifact(s) required by the active node.
4. Run the workflow step, or stop normally and let the project Stop hook run it:

```bash
agent-harness step --task <workflow.json> --hook-json
```

5. If the step blocks on failed checks, read the reason/report and repair the
   active artifact.
6. If the workflow advances, follow the next-node prompt.
7. Continue until `step` returns `systemMessage` instead of `decision: "block"`.

## Choosing Transitions

When state is `choosing`, do not guess from memory. Inspect the choices:

```bash
agent-harness options --state <state-path>
```

Then apply the transition that best matches the evidence:

```bash
agent-harness choose <transition-id> --state <state-path> --reason "<why this route is correct>"
```

The reason should cite the artifact evidence or failed/passed gate condition.

## Waiting For User

When state is `waiting`, identify what user authority is needed. Ask the user a
short concrete question. After the user decides, use:

```bash
agent-harness approve <node-id> --state <state-path> --reason "<reason>"
agent-harness reject <node-id> --state <state-path> --reason "<reason>"
```

Then run `step` again to apply the transition.

## Rules

- Respect the workflow's allowed statuses and domain routing rules.
- Do not pick the fastest advancing status unless the evidence justifies it.
- Do not edit workflow specs or check scripts during normal operation.
- Treat reports and state files as generated runtime data unless the project says
  otherwise.
- Keep user prompts short; the workflow and project `AGENTS.md` carry the
  protocol.

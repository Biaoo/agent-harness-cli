from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness_cli.config import load_json, write_json, write_json_atomic
from agent_harness_cli.reporting import make_report_id, next_actions, summarize_results
from agent_harness_cli.runners.run_checks import run_check_command
from agent_harness_cli.types import failure_result


WORKFLOW_COMMANDS = {
    "step",
    "status",
    "history",
    "options",
    "choose",
    "approve",
    "reject",
    "cancel",
    "reset-node",
    "validate-workflow",
}

WORKFLOW_COMMAND_DESCRIPTIONS = {
    "step": "Validate active workflow node(s), update state, and emit hook guidance.",
    "status": "Show the current workflow state.",
    "history": "Show workflow state transition history.",
    "options": "Show pending model-choice transitions.",
    "choose": "Choose and apply one pending transition.",
    "approve": "Approve a waiting human approval node.",
    "reject": "Reject a waiting human approval node.",
    "cancel": "Cancel a workflow state.",
    "reset-node": "Reset one node back to active.",
    "validate-workflow": "Validate a workflow JSON file.",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workflow_root_from_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.parent.name in {"tasks", "workflows"}:
        return resolved.parent.parent
    return resolved.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def unique_add(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def remove_value(values: list[str], item: str) -> None:
    while item in values:
        values.remove(item)


def normalize_initial(workflow: dict[str, Any]) -> list[str]:
    initial = workflow.get("initial")
    if isinstance(initial, str):
        return [initial]
    if isinstance(initial, list):
        return [str(item) for item in initial]
    nodes = workflow.get("nodes", [])
    if nodes and isinstance(nodes[0], dict):
        return [str(nodes[0].get("id", ""))]
    return []


def node_map(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = workflow.get("nodes", [])
    if not isinstance(nodes, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            mapped[str(node["id"])] = node
    return mapped


def default_state(workflow: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": str(workflow.get("id", "workflow")),
        "workflow_version": workflow.get("version", 1),
        "status": "running",
        "active": normalize_initial(workflow),
        "completed": [],
        "blocked": [],
        "waiting": [],
        "choosing": [],
        "failed": [],
        "choices": {},
        "artifacts": {},
        "locks": {},
        "user_decisions": {},
        "last_step": None,
        "history": [
            {
                "at": utc_now(),
                "event": "state_initialized",
                "active": normalize_initial(workflow),
            }
        ],
    }


def append_history(state: dict[str, Any], event: str, **data: Any) -> None:
    history = state.setdefault("history", [])
    if not isinstance(history, list):
        state["history"] = history = []
    entry = {"at": utc_now(), "event": event}
    entry.update(data)
    history.append(entry)


def default_state_path(root: Path, workflow: dict[str, Any]) -> Path:
    defaults = workflow.get("defaults", {})
    value = ".agent-harness/state.json"
    if isinstance(defaults, dict) and defaults.get("state_path"):
        value = str(defaults["state_path"])
    return resolve_path(root, value)


def default_report_dir(root: Path, workflow: dict[str, Any]) -> Path:
    defaults = workflow.get("defaults", {})
    value = "reports"
    if isinstance(defaults, dict) and defaults.get("report_dir"):
        value = str(defaults["report_dir"])
    return resolve_path(root, value)


def load_or_init_state(state_path: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    if state_path.exists():
        state = load_json(state_path)
    else:
        state = default_state(workflow)
        write_json_atomic(state_path, state)
    ensure_state_shape(state, workflow)
    return state


def ensure_state_shape(state: dict[str, Any], workflow: dict[str, Any]) -> None:
    state.setdefault("workflow_id", str(workflow.get("id", "workflow")))
    state.setdefault("workflow_version", workflow.get("version", 1))
    state.setdefault("status", "running")
    for key in ["active", "completed", "blocked", "waiting", "choosing", "failed"]:
        if not isinstance(state.get(key), list):
            state[key] = []
    for key in ["choices", "artifacts", "locks", "user_decisions"]:
        if not isinstance(state.get(key), dict):
            state[key] = {}
    if not isinstance(state.get("history"), list):
        state["history"] = []
    state.setdefault("last_step", None)


def validate_workflow(workflow: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if not workflow.get("id"):
        errors.append({"path": "id", "message": "Workflow must define id."})
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append({"path": "nodes", "message": "Workflow must define a non-empty nodes list."})
        return errors, warnings

    ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append({"path": f"nodes[{index}]", "message": "Each node must be an object."})
            continue
        node_id = node.get("id")
        if not node_id:
            errors.append({"path": f"nodes[{index}].id", "message": "Each node must define id."})
            continue
        node_id = str(node_id)
        if node_id in ids:
            errors.append({"path": f"nodes[{index}].id", "message": f"Duplicate node id: {node_id}."})
        ids.add(node_id)
        checks = node.get("checks", [])
        if checks is not None and not isinstance(checks, list):
            errors.append({"path": f"nodes[{index}].checks", "message": "Node checks must be a list."})
        if isinstance(checks, list):
            for check_index, check in enumerate(checks):
                if not isinstance(check, dict):
                    errors.append(
                        {
                            "path": f"nodes[{index}].checks[{check_index}]",
                            "message": "Each check must be an object.",
                        }
                    )
                    continue
                command = check.get("command")
                if command is not None and (
                    not isinstance(command, list) or not all(isinstance(part, str) for part in command)
                ):
                    errors.append(
                        {
                            "path": f"nodes[{index}].checks[{check_index}].command",
                            "message": "Check command must be a list of strings.",
                        }
                    )

    for initial in normalize_initial(workflow):
        if initial not in ids:
            errors.append({"path": "initial", "message": f"Initial node does not exist: {initial}."})

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        if node.get("type") == "join":
            if not as_list(node.get("wait_for")):
                errors.append(
                    {
                        "path": f"nodes[{index}].wait_for",
                        "message": "Join node must define wait_for.",
                    }
                )
            for dependency in as_list(node.get("wait_for")):
                dependency_id = str(dependency)
                if dependency_id not in ids:
                    errors.append(
                        {
                            "path": f"nodes[{index}].wait_for",
                            "message": f"Join dependency does not exist: {dependency_id}.",
                        }
                    )
        transitions = node.get("transitions", [])
        if transitions is None:
            transitions = []
        if not isinstance(transitions, list):
            errors.append({"path": f"nodes[{index}].transitions", "message": "Transitions must be a list."})
            continue
        for transition_index, transition in enumerate(transitions):
            if not isinstance(transition, dict):
                errors.append(
                    {
                        "path": f"nodes[{index}].transitions[{transition_index}]",
                        "message": "Each transition must be an object.",
                    }
                )
                continue
            if uses_failed_outcome_condition(transition.get("when", "passed")) and not (
                node.get("allow_failure_transition") or transition.get("allow_failure_transition")
            ):
                errors.append(
                    {
                        "path": f"nodes[{index}].transitions[{transition_index}].allow_failure_transition",
                        "message": "Failed transitions must explicitly set allow_failure_transition.",
                    }
                )
            for target in transition_targets(transition):
                if target not in ids:
                    errors.append(
                        {
                            "path": f"nodes[{index}].transitions[{transition_index}].to",
                            "message": f"Transition target does not exist: {target}.",
                        }
                    )
        policy = node.get("decision_policy", {})
        if isinstance(policy, dict):
            mode = policy.get("mode")
            if mode and mode not in {"auto", "model_choice"}:
                warnings.append(
                    {
                        "path": f"nodes[{index}].decision_policy.mode",
                        "message": f"Unknown decision policy mode: {mode}.",
                    }
                )

    return errors, warnings


def transition_action(transition: dict[str, Any]) -> str:
    if transition.get("action"):
        return str(transition["action"])
    if transition.get("to") is not None or transition.get("targets") is not None:
        return "advance"
    return "complete"


def transition_targets(transition: dict[str, Any]) -> list[str]:
    action = transition_action(transition)
    if action not in {"advance", "branch", "fan_out", "join"}:
        return []
    target_value = transition.get("targets") if action == "fan_out" and "targets" in transition else transition.get("to")
    targets: list[str] = []
    for target in as_list(target_value):
        if target is not None:
            targets.append(str(target))
    return targets


def task_for_node(workflow: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{workflow.get('id', 'workflow')}:{node.get('id', 'node')}",
        "description": node.get("title", node.get("id", "workflow node")),
        "checks": node.get("checks", []),
        "workflow_id": workflow.get("id"),
        "workflow_version": workflow.get("version", 1),
        "node_id": node.get("id"),
    }


def run_node_checks(
    *,
    root: Path,
    workflow_path: Path,
    workflow: dict[str, Any],
    state: dict[str, Any],
    node: dict[str, Any],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    checks = node.get("checks", [])
    if checks is None:
        checks = []
    if not isinstance(checks, list):
        return [
            failure_result(
                check="invalid_node_checks",
                severity="error",
                summary="Node checks are not configured correctly.",
                message="Node checks must be a list.",
                suggestion="Update the workflow node so checks is a list.",
            )
        ]

    task = task_for_node(workflow, node)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agent-harness-workflow-") as temp_dir:
        input_dir = Path(temp_dir)
        for check_config in checks:
            if not isinstance(check_config, dict):
                results.append(
                    failure_result(
                        check="invalid_check",
                        severity="error",
                        summary="Node contains an invalid check entry.",
                        message="Each checks item must be an object.",
                        suggestion="Replace invalid check entries with objects containing name and command.",
                    )
                )
                continue
            results.append(
                run_check_command(
                    root=root,
                    task_path=workflow_path,
                    task=task,
                    check_config=check_config,
                    input_dir=input_dir,
                    timeout_seconds=timeout_seconds,
                    context={
                        "workflow": workflow,
                        "state": state,
                        "node": node,
                        "artifacts": state.get("artifacts", {}),
                    },
                )
            )
    return results


def check_outcome(results: list[dict[str, Any]]) -> str:
    summary = summarize_results(results)
    if summary["blocking_failures"]:
        return "failed"
    if summary["warning_failures"]:
        return "warning_only"
    return "passed"


def strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def parse_literal(value: str) -> Any:
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if text.lower() == "null":
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def get_path_value(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def compare_values(left: Any, operator: str, right: Any) -> bool:
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            left = float(left)
            right = float(right)
        except (TypeError, ValueError):
            return False
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    return False


def atom_matches(atom: str, context: dict[str, Any]) -> bool:
    text = atom.strip()
    if not text:
        return False
    if text == "always":
        return True
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "passed":
        return context.get("outcome") in {"passed", "warning_only"}
    if text in {"failed", "warning_only"}:
        return context.get("outcome") == text
    if text in {"user.approved", "user.rejected"}:
        return context.get("user_decision") == text.split(".", 1)[1]

    contains_match = re.fullmatch(r"(state\.[A-Za-z0-9_.-]+)\s+contains\s+(.+)", text)
    if contains_match:
        values = get_path_value(context, contains_match.group(1))
        expected = strip_quotes(contains_match.group(2))
        return isinstance(values, list) and expected in values

    exists_match = re.fullmatch(r"(artifact|artifacts)\.([A-Za-z0-9_.\-/]+)\.exists", text)
    if exists_match:
        artifacts = context.get("artifacts", {})
        return get_path_value(artifacts, exists_match.group(2)) is not None

    comparison_match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*(==|!=|>=|<=|>|<)\s*(.+)", text)
    if comparison_match:
        left_path, operator, right_text = comparison_match.groups()
        return compare_values(get_path_value(context, left_path), operator, parse_literal(right_text))

    return False


def condition_matches(condition: Any, context: dict[str, Any]) -> bool:
    text = str(condition or "passed").strip()
    for or_part in text.split("||"):
        atoms = [part.strip() for part in or_part.split("&&")]
        if atoms and all(atom_matches(atom, context) for atom in atoms):
            return True
    return False


def uses_failed_outcome_condition(condition: Any) -> bool:
    text = str(condition or "passed").strip()
    atoms: list[str] = []
    for or_part in text.split("||"):
        atoms.extend(part.strip() for part in or_part.split("&&"))
    return any(atom == "failed" for atom in atoms)


def checks_by_name(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(result.get("check", "")): result for result in results}


def candidate_transitions(
    *,
    node: dict[str, Any],
    state: dict[str, Any],
    results: list[dict[str, Any]],
    outcome: str,
    user_decision: str | None = None,
) -> list[dict[str, Any]]:
    transitions = node.get("transitions", [])
    if not isinstance(transitions, list):
        return []
    context = {
        "outcome": outcome,
        "user_decision": user_decision,
        "state": state,
        "checks": checks_by_name(results),
        "artifacts": state.get("artifacts", {}),
    }
    matched: list[dict[str, Any]] = []
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        if outcome == "failed" and not (node.get("allow_failure_transition") or transition.get("allow_failure_transition")):
            continue
        if condition_matches(transition.get("when", "passed"), context):
            matched.append(transition)
    return matched


def make_default_transition(node: dict[str, Any], outcome: str) -> dict[str, Any] | None:
    if outcome != "passed":
        return None
    if str(node.get("type", "stage")) == "terminal":
        return {"id": "complete", "when": "passed", "action": "complete", "label": "Complete workflow"}
    return None


def choice_required(node: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
    policy = node.get("decision_policy", {})
    if not isinstance(policy, dict):
        return False
    if policy.get("mode") != "model_choice":
        return False
    return bool(policy.get("always_require_choice")) or len(candidates) > 1


def transition_display(transition: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(transition.get("id") or transition.get("label") or transition_action(transition)),
        "action": transition_action(transition),
        "label": str(transition.get("label") or transition.get("id") or transition_action(transition)),
    }
    targets = transition_targets(transition)
    if targets:
        data["to"] = targets if len(targets) > 1 else targets[0]
    if transition.get("prompt"):
        data["prompt"] = str(transition["prompt"])
    return data


def enter_choice(
    *,
    state: dict[str, Any],
    node: dict[str, Any],
    candidates: list[dict[str, Any]],
    outcome: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    node_id = str(node["id"])
    remove_value(state["active"], node_id)
    unique_add(state["choosing"], node_id)
    state["status"] = "choosing"
    options = [transition_display(transition) for transition in candidates]
    state["choices"][node_id] = {
        "status": "pending",
        "node_id": node_id,
        "outcome": outcome,
        "options": options,
        "transitions": candidates,
        "selected": None,
        "reason": None,
        "created_at": utc_now(),
        "check_summary": summarize_results(results),
    }
    append_history(state, "choice_required", node_id=node_id, options=[option["id"] for option in options])
    return state["choices"][node_id]


def apply_transition(
    *,
    state: dict[str, Any],
    node_id: str,
    transition: dict[str, Any],
    outcome: str = "passed",
    reason: str | None = None,
) -> dict[str, Any]:
    action = transition_action(transition)
    transition_id = str(transition.get("id") or action)

    remove_value(state["active"], node_id)
    remove_value(state["blocked"], node_id)
    remove_value(state["waiting"], node_id)
    remove_value(state["choosing"], node_id)

    if action == "repeat":
        unique_add(state["active"], node_id)
        state["status"] = "running"
    elif action in {"advance", "branch", "fan_out", "join"}:
        if outcome != "failed":
            unique_add(state["completed"], node_id)
        for target in transition_targets(transition):
            if target not in state["completed"]:
                unique_add(state["active"], target)
        state["status"] = "running"
    elif action == "wait_for_user":
        unique_add(state["waiting"], node_id)
        state["status"] = "waiting"
    elif action == "complete":
        if outcome != "failed":
            unique_add(state["completed"], node_id)
        if not state["active"] and not state["waiting"] and not state["choosing"]:
            state["status"] = "completed"
        else:
            state["status"] = "running"
    elif action == "fail":
        unique_add(state["failed"], node_id)
        unique_add(state["blocked"], node_id)
        state["status"] = "failed"
    elif action == "handoff":
        if outcome != "failed":
            unique_add(state["completed"], node_id)
        state["status"] = "handoff"
    else:
        unique_add(state["blocked"], node_id)
        state["status"] = "failed"

    if state["status"] == "running" and not state["active"] and not state["waiting"] and not state["choosing"]:
        state["status"] = "completed"

    state["last_step"] = {
        "at": utc_now(),
        "node_id": node_id,
        "transition_id": transition_id,
        "action": action,
        "outcome": outcome,
    }
    append_history(
        state,
        "transition_applied",
        node_id=node_id,
        transition_id=transition_id,
        action=action,
        outcome=outcome,
        reason=reason,
    )
    return {
        "id": transition_id,
        "action": action,
        "to": transition_targets(transition),
        "label": transition.get("label"),
        "prompt": transition.get("prompt"),
    }


def block_message_for_failure(node: dict[str, Any], results: list[dict[str, Any]], report_id: str) -> str:
    title = node.get("title") or node.get("id")
    actions = next_actions(results)
    lines = [
        f"Workflow check blocked at node '{node.get('id')}' ({title}).",
        f"Report: agent-harness view {report_id}",
    ]
    if actions:
        lines.append("Required fixes:")
        lines.extend(f"- {action}" for action in actions)
    else:
        lines.append("Inspect the report and fix the failed checks before continuing.")
    return "\n".join(lines)


def block_message_for_choice(choice: dict[str, Any], state_path: Path) -> str:
    node_id = choice["node_id"]
    lines = [f"Node '{node_id}' passed. Choose the next transition:"]
    for option in choice.get("options", []):
        line = f"- {option['id']}: {option.get('label', option['id'])}"
        if option.get("to"):
            line += f" -> {option['to']}"
        if option.get("prompt"):
            line += f"\n  {option['prompt']}"
        lines.append(line)
    lines.append("")
    lines.append(f"Inspect choices: agent-harness options --state {state_path}")
    lines.append(f"Choose: agent-harness choose <transition-id> --state {state_path} --reason \"...\"")
    return "\n".join(lines)


def block_message_for_advance(node: dict[str, Any], transition: dict[str, Any], state: dict[str, Any]) -> str:
    lines = [
        f"Node '{node.get('id')}' passed.",
        f"Applied transition: {transition.get('id')} ({transition.get('action')}).",
    ]
    if transition.get("prompt"):
        lines.append(str(transition["prompt"]))
    if state.get("active"):
        lines.append(f"Active node(s): {', '.join(state['active'])}")
    if state.get("status") == "completed":
        lines.append("Workflow completed.")
    return "\n".join(lines)


def block_message_for_waiting(node: dict[str, Any], state_path: Path) -> str:
    title = node.get("title") or node.get("id")
    return "\n".join(
        [
            f"Workflow is waiting at node '{node.get('id')}' ({title}).",
            f"Approve: agent-harness approve {node.get('id')} --state {state_path}",
            f"Reject: agent-harness reject {node.get('id')} --state {state_path} --reason \"...\"",
        ]
    )


def hook_payload(*, block: bool, message: str) -> dict[str, Any]:
    if block:
        return {"decision": "block", "reason": message}
    return {"systemMessage": message}


def node_report(
    *,
    node: dict[str, Any],
    results: list[dict[str, Any]],
    outcome: str,
    transition: dict[str, Any] | None = None,
    choice: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "node_id": node.get("id"),
        "node_type": node.get("type", "stage"),
        "title": node.get("title"),
        "outcome": outcome,
        "summary": summarize_results(results),
        "checks": results,
    }
    if transition:
        data["transition"] = transition
    if choice:
        data["choice"] = {
            "node_id": choice.get("node_id"),
            "options": choice.get("options", []),
            "status": choice.get("status"),
        }
    if message:
        data["message"] = message
    return data


def write_workflow_report(
    *,
    root: Path,
    workflow_path: Path,
    workflow: dict[str, Any],
    state_path: Path,
    report_dir: Path,
    report_id: str,
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    node_reports: list[dict[str, Any]],
    hook: dict[str, Any],
) -> Path:
    checks: list[dict[str, Any]] = []
    for report in node_reports:
        checks.extend(report.get("checks", []))
    summary = summarize_results(checks)
    report = {
        "report_id": report_id,
        "task_id": workflow.get("id", "workflow"),
        "task_path": str(workflow_path),
        "workflow_id": workflow.get("id", "workflow"),
        "workflow_version": workflow.get("version", 1),
        "state_path": str(state_path),
        "created_at": utc_now(),
        "passed": summary["blocking_failures"] == 0 and state_after.get("status") not in {"failed"},
        "summary": summary,
        "checks": checks,
        "nodes": node_reports,
        "next_actions": next_actions(checks),
        "state_before": state_before,
        "state_after": {
            "status": state_after.get("status"),
            "active": state_after.get("active", []),
            "completed": state_after.get("completed", []),
            "blocked": state_after.get("blocked", []),
            "waiting": state_after.get("waiting", []),
            "choosing": state_after.get("choosing", []),
            "failed": state_after.get("failed", []),
        },
        "hook": hook,
    }
    path = report_dir / f"{report_id}.json"
    write_json(path, report)
    return path.resolve()


def run_human_approval_node(
    *,
    node: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    node_id = str(node["id"])
    decisions = state.setdefault("user_decisions", {})
    decision_record = decisions.get(node_id)
    if isinstance(decision_record, dict) and decision_record.get("decision") in {"approved", "rejected"}:
        decision = str(decision_record["decision"])
        candidates = candidate_transitions(
            node=node,
            state=state,
            results=[],
            outcome="passed",
            user_decision=decision,
        )
        transition = candidates[0] if candidates else None
        if transition is None:
            transition = {"id": decision, "when": f"user.{decision}", "action": "complete"}
        applied = apply_transition(
            state=state,
            node_id=node_id,
            transition=transition,
            outcome="passed",
            reason=decision_record.get("reason"),
        )
        return (
            node_report(node=node, results=[], outcome=f"user_{decision}", transition=applied),
            hook_payload(block=True, message=block_message_for_advance(node, applied, state)),
        )

    remove_value(state["active"], node_id)
    unique_add(state["waiting"], node_id)
    state["status"] = "waiting"
    append_history(state, "waiting_for_user", node_id=node_id)
    message = block_message_for_waiting(node, state_path)
    return (
        node_report(node=node, results=[], outcome="waiting", message=message),
        hook_payload(block=True, message=message),
    )


def join_is_ready(node: dict[str, Any], state: dict[str, Any]) -> bool:
    wait_for = [str(item) for item in as_list(node.get("wait_for"))]
    return bool(wait_for) and all(item in state.get("completed", []) for item in wait_for)


def run_join_node(
    *,
    node: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    node_id = str(node["id"])
    if not join_is_ready(node, state):
        unique_add(state["waiting"], node_id)
        if not [active for active in state["active"] if active != node_id]:
            state["status"] = "waiting"
        message = f"Join node '{node_id}' is waiting for: {', '.join(as_list(node.get('wait_for')))}."
        message += f"\nCurrent state: agent-harness status --state {state_path}"
        append_history(state, "join_waiting", node_id=node_id, wait_for=as_list(node.get("wait_for")))
        return node_report(node=node, results=[], outcome="waiting", message=message), hook_payload(
            block=True, message=message
        )

    remove_value(state["waiting"], node_id)
    transition = first_transition_or_default(node, state, [], "passed")
    if transition is None:
        transition = {"id": "join_complete", "action": "complete", "when": "passed"}
    applied = apply_transition(state=state, node_id=node_id, transition=transition, outcome="passed")
    return (
        node_report(node=node, results=[], outcome="passed", transition=applied),
        hook_payload(block=True, message=block_message_for_advance(node, applied, state)),
    )


def first_transition_or_default(
    node: dict[str, Any],
    state: dict[str, Any],
    results: list[dict[str, Any]],
    outcome: str,
) -> dict[str, Any] | None:
    candidates = candidate_transitions(node=node, state=state, results=results, outcome=outcome)
    if candidates:
        return candidates[0]
    return make_default_transition(node, outcome)


def run_standard_node(
    *,
    root: Path,
    workflow_path: Path,
    workflow: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    node: dict[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    node_id = str(node["id"])
    results = run_node_checks(
        root=root,
        workflow_path=workflow_path,
        workflow=workflow,
        state=state,
        node=node,
        timeout_seconds=timeout_seconds,
    )
    outcome = check_outcome(results)

    candidates = candidate_transitions(node=node, state=state, results=results, outcome=outcome)
    default_transition = make_default_transition(node, outcome)
    if default_transition is not None and not candidates:
        candidates = [default_transition]

    if outcome == "failed" and not candidates:
        unique_add(state["blocked"], node_id)
        state["status"] = "failed"
        append_history(state, "node_blocked", node_id=node_id, summary=summarize_results(results))
        message = block_message_for_failure(node, results, "latest")
        return node_report(node=node, results=results, outcome=outcome, message=message), hook_payload(
            block=True, message=message
        )

    if not candidates:
        unique_add(state["blocked"], node_id)
        state["status"] = "failed"
        result = failure_result(
            check="workflow_transition",
            severity="error",
            summary="No matching transition was found.",
            message=f"Node '{node_id}' passed, but no transition matched.",
            suggestion="Add a matching transition or mark the node as terminal.",
            evidence={"node_id": node_id, "outcome": outcome},
        )
        message = "Workflow configuration blocked progression: no matching transition."
        return node_report(node=node, results=results + [result], outcome="failed", message=message), hook_payload(
            block=True, message=message
        )

    if choice_required(node, candidates):
        choice = enter_choice(state=state, node=node, candidates=candidates, outcome=outcome, results=results)
        message = block_message_for_choice(choice, state_path)
        return node_report(node=node, results=results, outcome=outcome, choice=choice), hook_payload(
            block=True, message=message
        )

    applied = apply_transition(state=state, node_id=node_id, transition=candidates[0], outcome=outcome)
    return (
        node_report(node=node, results=results, outcome=outcome, transition=applied),
        hook_payload(block=True, message=block_message_for_advance(node, applied, state)),
    )


def run_active_node(
    *,
    root: Path,
    workflow_path: Path,
    workflow: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    node: dict[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    node_type = str(node.get("type", "stage"))
    if node_type == "human_approval":
        return run_human_approval_node(node=node, state=state, state_path=state_path)
    if node_type == "join":
        return run_join_node(node=node, state=state, state_path=state_path)
    return run_standard_node(
        root=root,
        workflow_path=workflow_path,
        workflow=workflow,
        state=state,
        state_path=state_path,
        node=node,
        timeout_seconds=timeout_seconds,
    )


def compact_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": state.get("workflow_id"),
        "workflow_version": state.get("workflow_version"),
        "status": state.get("status"),
        "active": list(state.get("active", [])),
        "completed": list(state.get("completed", [])),
        "blocked": list(state.get("blocked", [])),
        "waiting": list(state.get("waiting", [])),
        "choosing": list(state.get("choosing", [])),
        "failed": list(state.get("failed", [])),
        "last_step": state.get("last_step"),
    }


def pending_choice(state: dict[str, Any]) -> dict[str, Any] | None:
    choices = state.get("choices", {})
    if not isinstance(choices, dict):
        return None
    for node_id in state.get("choosing", []):
        choice = choices.get(node_id)
        if isinstance(choice, dict) and choice.get("status") == "pending":
            return choice
    for choice in choices.values():
        if isinstance(choice, dict) and choice.get("status") == "pending":
            return choice
    return None


def format_status(state: dict[str, Any], state_path: Path) -> str:
    lines = [
        f"state_path: {state_path}",
        f"workflow_id: {state.get('workflow_id')}",
        f"status: {state.get('status')}",
        f"active: {', '.join(state.get('active', [])) or '-'}",
        f"completed: {', '.join(state.get('completed', [])) or '-'}",
        f"waiting: {', '.join(state.get('waiting', [])) or '-'}",
        f"choosing: {', '.join(state.get('choosing', [])) or '-'}",
        f"blocked: {', '.join(state.get('blocked', [])) or '-'}",
        f"failed: {', '.join(state.get('failed', [])) or '-'}",
    ]
    choice = pending_choice(state)
    if choice:
        lines.append("")
        lines.append(block_message_for_choice(choice, state_path))
    return "\n".join(lines)


def output_result(payload: dict[str, Any], *, as_json: bool, hook_json: bool) -> None:
    if hook_json:
        print(json.dumps(payload["hook"], indent=2, ensure_ascii=False))
    elif as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(payload.get("message", ""))


def step_command(args: argparse.Namespace) -> int:
    workflow_path = Path(args.task).resolve()
    workflow = load_json(workflow_path)
    errors, warnings = validate_workflow(workflow)
    if errors:
        if args.hook_json:
            print(
                json.dumps(
                    hook_payload(
                        block=True,
                        message="Workflow validation failed: "
                        + "; ".join(f"{error['path']}: {error['message']}" for error in errors),
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps({"errors": errors, "warnings": warnings}, indent=2, ensure_ascii=False))
        return 2

    root = workflow_root_from_path(workflow_path)
    state_path = resolve_path(root, args.state) if args.state else default_state_path(root, workflow)
    report_dir = resolve_path(root, args.report_dir) if args.report_dir else default_report_dir(root, workflow)
    report_id = args.report_id or make_report_id(str(workflow.get("id", "workflow")))
    state = load_or_init_state(state_path, workflow)
    state_before = json.loads(json.dumps(compact_state(state)))
    nodes = node_map(workflow)
    node_reports: list[dict[str, Any]] = []

    if state.get("status") == "completed":
        hook = hook_payload(block=False, message="Agent harness workflow completed.")
        report_path = write_workflow_report(
            root=root,
            workflow_path=workflow_path,
            workflow=workflow,
            state_path=state_path,
            report_dir=report_dir,
            report_id=report_id,
            state_before=state_before,
            state_after=state,
            node_reports=node_reports,
            hook=hook,
        )
        output_result(
            {
                "report_id": report_id,
                "report_path": str(report_path),
                "state": compact_state(state),
                "hook": hook,
                "message": "Workflow completed.",
            },
            as_json=args.json,
            hook_json=args.hook_json,
        )
        return 0

    if state.get("status") == "cancelled":
        hook = hook_payload(block=False, message="Agent harness workflow is cancelled.")
        report_path = write_workflow_report(
            root=root,
            workflow_path=workflow_path,
            workflow=workflow,
            state_path=state_path,
            report_dir=report_dir,
            report_id=report_id,
            state_before=state_before,
            state_after=state,
            node_reports=node_reports,
            hook=hook,
        )
        output_result(
            {
                "report_id": report_id,
                "report_path": str(report_path),
                "state": compact_state(state),
                "hook": hook,
                "message": "Workflow cancelled.",
            },
            as_json=args.json,
            hook_json=args.hook_json,
        )
        return 0

    choice = pending_choice(state)
    if choice:
        hook = hook_payload(block=True, message=block_message_for_choice(choice, state_path))
        report_path = write_workflow_report(
            root=root,
            workflow_path=workflow_path,
            workflow=workflow,
            state_path=state_path,
            report_dir=report_dir,
            report_id=report_id,
            state_before=state_before,
            state_after=state,
            node_reports=node_reports,
            hook=hook,
        )
        output_result(
            {
                "report_id": report_id,
                "report_path": str(report_path),
                "state": compact_state(state),
                "hook": hook,
                "message": hook["reason"],
            },
            as_json=args.json,
            hook_json=args.hook_json,
        )
        return 0

    active_snapshot = [args.node] if args.node else list(state.get("active", []))
    if args.node and args.node not in state.get("active", []):
        hook = hook_payload(block=True, message=f"Node '{args.node}' is not active.")
        report_path = write_workflow_report(
            root=root,
            workflow_path=workflow_path,
            workflow=workflow,
            state_path=state_path,
            report_dir=report_dir,
            report_id=report_id,
            state_before=state_before,
            state_after=state,
            node_reports=node_reports,
            hook=hook,
        )
        output_result(
            {
                "report_id": report_id,
                "report_path": str(report_path),
                "state": compact_state(state),
                "hook": hook,
                "message": hook["reason"],
            },
            as_json=args.json,
            hook_json=args.hook_json,
        )
        return 0 if args.hook_json else 1
    if not active_snapshot:
        waiting = state.get("waiting", [])
        if waiting:
            node = nodes.get(str(waiting[0]), {"id": str(waiting[0]), "title": str(waiting[0])})
            hook = hook_payload(block=True, message=block_message_for_waiting(node, state_path))
        else:
            state["status"] = "completed"
            hook = hook_payload(block=False, message="Agent harness workflow completed.")
        write_json_atomic(state_path, state)
        report_path = write_workflow_report(
            root=root,
            workflow_path=workflow_path,
            workflow=workflow,
            state_path=state_path,
            report_dir=report_dir,
            report_id=report_id,
            state_before=state_before,
            state_after=state,
            node_reports=node_reports,
            hook=hook,
        )
        output_result(
            {
                "report_id": report_id,
                "report_path": str(report_path),
                "state": compact_state(state),
                "hook": hook,
                "message": hook.get("reason") or hook.get("systemMessage", ""),
            },
            as_json=args.json,
            hook_json=args.hook_json,
        )
        return 0

    hook = hook_payload(block=False, message="No active workflow node was processed.")
    for node_id in active_snapshot:
        if node_id not in state.get("active", []):
            continue
        node = nodes.get(str(node_id))
        if node is None:
            result = failure_result(
                check="workflow_node",
                severity="error",
                summary="Active node is not defined in workflow.",
                message=f"Active node does not exist: {node_id}.",
                suggestion="Reset the state or add the missing node to the workflow.",
            )
            state["status"] = "failed"
            unique_add(state["blocked"], str(node_id))
            node_reports.append(
                {
                    "node_id": node_id,
                    "node_type": "unknown",
                    "outcome": "failed",
                    "summary": summarize_results([result]),
                    "checks": [result],
                }
            )
            hook = hook_payload(block=True, message=f"Workflow state references missing node '{node_id}'.")
            break
        report, hook = run_active_node(
            root=root,
            workflow_path=workflow_path,
            workflow=workflow,
            state=state,
            state_path=state_path,
            node=node,
            timeout_seconds=args.timeout,
        )
        node_reports.append(report)
        if state.get("status") in {"failed", "waiting", "choosing", "handoff"}:
            break

    if state.get("status") == "completed":
        hook = hook_payload(block=False, message="Agent harness workflow completed.")

    write_json_atomic(state_path, state)
    if node_reports and "Report: agent-harness view latest" in hook.get("reason", ""):
        view_command = f"agent-harness view {report_id}"
        if report_dir != root / "reports":
            view_command += f" --report-dir {report_dir}"
        hook["reason"] = hook["reason"].replace("agent-harness view latest", view_command)
    report_path = write_workflow_report(
        root=root,
        workflow_path=workflow_path,
        workflow=workflow,
        state_path=state_path,
        report_dir=report_dir,
        report_id=report_id,
        state_before=state_before,
        state_after=state,
        node_reports=node_reports,
        hook=hook,
    )
    payload = {
        "report_id": report_id,
        "report_path": str(report_path),
        "state_path": str(state_path),
        "state": compact_state(state),
        "nodes": node_reports,
        "hook": hook,
        "message": hook.get("reason") or hook.get("systemMessage", ""),
    }
    output_result(payload, as_json=args.json, hook_json=args.hook_json)
    if args.hook_json:
        return 0
    return 1 if state.get("status") == "failed" else 0


def status_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    if args.json:
        print(json.dumps({"state_path": str(state_path), "state": state}, indent=2, ensure_ascii=False))
    else:
        print(format_status(state, state_path))
    return 0


def history_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    history = state.get("history", [])
    if args.json:
        print(json.dumps({"state_path": str(state_path), "history": history}, indent=2, ensure_ascii=False))
    else:
        for item in history:
            print(f"{item.get('at')} {item.get('event')} {json.dumps(item, ensure_ascii=False)}")
    return 0


def options_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    choice = pending_choice(state)
    payload = {"state_path": str(state_path), "choice": choice}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif choice:
        print(block_message_for_choice(choice, state_path))
    else:
        print("No pending workflow choices.")
    return 0


def choose_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    choice = pending_choice(state)
    if not choice:
        print("No pending workflow choices.")
        return 1
    node_id = str(choice["node_id"])
    selected: dict[str, Any] | None = None
    for transition in choice.get("transitions", []):
        transition_id = str(transition.get("id") or transition_action(transition))
        if transition_id == args.transition_id:
            selected = transition
            break
    if selected is None:
        print(f"Unknown transition choice: {args.transition_id}")
        return 1

    choice["selected"] = args.transition_id
    choice["reason"] = args.reason
    choice["status"] = "selected"
    choice["selected_at"] = utc_now()
    applied = apply_transition(
        state=state,
        node_id=node_id,
        transition=selected,
        outcome=str(choice.get("outcome", "passed")),
        reason=args.reason,
    )
    append_history(state, "choice_selected", node_id=node_id, transition_id=args.transition_id, reason=args.reason)
    write_json_atomic(state_path, state)
    payload = {
        "state_path": str(state_path),
        "node_id": node_id,
        "transition": applied,
        "state": compact_state(state),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(block_message_for_advance({"id": node_id}, applied, state))
    return 0


def approval_command(args: argparse.Namespace, decision: str) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    ensure_state_shape(state, {"id": state.get("workflow_id", "workflow")})
    node_id = args.node_id
    state["user_decisions"][node_id] = {
        "decision": decision,
        "reason": args.reason,
        "at": utc_now(),
    }
    remove_value(state["waiting"], node_id)
    unique_add(state["active"], node_id)
    state["status"] = "running"
    append_history(state, f"user_{decision}", node_id=node_id, reason=args.reason)
    write_json_atomic(state_path, state)
    payload = {"state_path": str(state_path), "node_id": node_id, "decision": decision, "state": compact_state(state)}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Recorded {decision} for node '{node_id}'. Run agent-harness step to apply the transition.")
    return 0


def cancel_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    state["status"] = "cancelled"
    append_history(state, "cancelled", reason=args.reason)
    write_json_atomic(state_path, state)
    if args.json:
        print(json.dumps({"state_path": str(state_path), "state": compact_state(state)}, indent=2, ensure_ascii=False))
    else:
        print(f"Workflow cancelled: {state_path}")
    return 0


def reset_node_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state).resolve()
    state = load_json(state_path)
    ensure_state_shape(state, {"id": state.get("workflow_id", "workflow")})
    node_id = args.node_id
    for key in ["completed", "blocked", "waiting", "choosing", "failed"]:
        remove_value(state[key], node_id)
    unique_add(state["active"], node_id)
    if isinstance(state.get("choices"), dict):
        state["choices"].pop(node_id, None)
    if isinstance(state.get("user_decisions"), dict):
        state["user_decisions"].pop(node_id, None)
    state["status"] = "running"
    append_history(state, "node_reset", node_id=node_id, reason=args.reason)
    write_json_atomic(state_path, state)
    if args.json:
        print(json.dumps({"state_path": str(state_path), "state": compact_state(state)}, indent=2, ensure_ascii=False))
    else:
        print(f"Reset node '{node_id}'.")
    return 0


def validate_command(args: argparse.Namespace) -> int:
    workflow_path = Path(args.task).resolve()
    workflow = load_json(workflow_path)
    errors, warnings = validate_workflow(workflow)
    payload = {"task_path": str(workflow_path), "valid": not errors, "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if errors:
            print("Workflow validation failed.")
            for error in errors:
                print(f"- {error['path']}: {error['message']}")
        else:
            print("Workflow validation passed.")
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"- {warning['path']}: {warning['message']}")
    return 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control a multi-stage agent harness workflow.")
    subparsers = parser.add_subparsers(dest="command")

    step = subparsers.add_parser("step", description="Validate active workflow node(s) and update state.")
    step.add_argument("--task", required=True, help="Path to a workflow JSON file.")
    step.add_argument("--state", help="Workflow state path. Defaults to workflow defaults.state_path.")
    step.add_argument("--report-dir", help="Directory for workflow reports. Defaults to workflow defaults.report_dir.")
    step.add_argument("--report-id", help="Optional stable report id.")
    step.add_argument("--node", help="Limit this step to one active node.")
    step.add_argument("--timeout", type=float, default=120.0, help="Timeout per check command in seconds.")
    step.add_argument("--hook-json", action="store_true", help="Print Codex hook decision JSON.")
    step.add_argument("--json", action="store_true", help="Print machine-readable workflow step result.")

    status = subparsers.add_parser("status", description="Show workflow state.")
    status.add_argument("--state", default=".agent-harness/state.json")
    status.add_argument("--json", action="store_true")

    history = subparsers.add_parser("history", description="Show workflow state history.")
    history.add_argument("--state", default=".agent-harness/state.json")
    history.add_argument("--json", action="store_true")

    options = subparsers.add_parser("options", description="Show pending model-choice transitions.")
    options.add_argument("--state", default=".agent-harness/state.json")
    options.add_argument("--json", action="store_true")

    choose = subparsers.add_parser("choose", description="Choose and apply a pending transition.")
    choose.add_argument("transition_id")
    choose.add_argument("--state", default=".agent-harness/state.json")
    choose.add_argument("--reason", required=True)
    choose.add_argument("--json", action="store_true")

    approve = subparsers.add_parser("approve", description="Approve a waiting human approval node.")
    approve.add_argument("node_id")
    approve.add_argument("--state", default=".agent-harness/state.json")
    approve.add_argument("--reason")
    approve.add_argument("--json", action="store_true")

    reject = subparsers.add_parser("reject", description="Reject a waiting human approval node.")
    reject.add_argument("node_id")
    reject.add_argument("--state", default=".agent-harness/state.json")
    reject.add_argument("--reason", required=True)
    reject.add_argument("--json", action="store_true")

    cancel = subparsers.add_parser("cancel", description="Cancel a workflow state.")
    cancel.add_argument("--state", default=".agent-harness/state.json")
    cancel.add_argument("--reason")
    cancel.add_argument("--json", action="store_true")

    reset_node = subparsers.add_parser("reset-node", description="Reset one node back to active.")
    reset_node.add_argument("node_id")
    reset_node.add_argument("--state", default=".agent-harness/state.json")
    reset_node.add_argument("--reason")
    reset_node.add_argument("--json", action="store_true")

    validate = subparsers.add_parser("validate-workflow", description="Validate a workflow JSON file.")
    validate.add_argument("--task", required=True)
    validate.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "step":
        return step_command(args)
    if args.command == "status":
        return status_command(args)
    if args.command == "history":
        return history_command(args)
    if args.command == "options":
        return options_command(args)
    if args.command == "choose":
        return choose_command(args)
    if args.command == "approve":
        return approval_command(args, "approved")
    if args.command == "reject":
        return approval_command(args, "rejected")
    if args.command == "cancel":
        return cancel_command(args)
    if args.command == "reset-node":
        return reset_node_command(args)
    if args.command == "validate-workflow":
        return validate_command(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

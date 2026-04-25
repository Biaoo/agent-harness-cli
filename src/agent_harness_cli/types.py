from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


Severity = str


@dataclass(frozen=True)
class Reason:
    message: str
    file: str | None = None
    line: int | None = None
    suggestion: str | None = None
    requires_user_input: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "message": self.message,
            "requires_user_input": self.requires_user_input,
        }
        if self.file is not None:
            data["file"] = self.file
        if self.line is not None:
            data["line"] = self.line
        if self.suggestion is not None:
            data["suggestion"] = self.suggestion
        if self.evidence:
            data["evidence"] = self.evidence
        return data


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    severity: Severity
    summary: str
    score: float | None = None
    reasons: list[Reason] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "check": self.check,
            "passed": self.passed,
            "severity": self.severity,
            "summary": self.summary,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }
        if self.score is not None:
            data["score"] = self.score
        if self.metadata:
            data["metadata"] = self.metadata
        return data


@dataclass(frozen=True)
class HarnessContext:
    root: Path
    task: dict[str, Any]

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root / path

    @property
    def task_id(self) -> str:
        return str(self.task.get("id", "unknown_task"))


def failure_result(
    *,
    check: str,
    severity: str,
    summary: str,
    message: str,
    suggestion: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return CheckResult(
        check=check,
        passed=False,
        severity=severity,
        score=0.0,
        summary=summary,
        reasons=[
            Reason(
                message=message,
                suggestion=suggestion,
                evidence=evidence or {},
            )
        ],
    ).to_dict()


def normalize_check_result(result: dict[str, Any], check_config: dict[str, Any]) -> dict[str, Any]:
    name = str(result.get("check") or check_config.get("name") or "unnamed_check")
    severity = str(result.get("severity") or check_config.get("severity") or "error")
    reasons = result.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = [
            {
                "message": "Check returned a non-list reasons field.",
                "requires_user_input": False,
            }
        ]

    normalized = {
        "check": name,
        "passed": bool(result.get("passed", False)),
        "severity": severity,
        "summary": str(result.get("summary") or "No summary returned."),
        "reasons": [normalize_reason(reason) for reason in reasons],
    }
    if "score" in result:
        normalized["score"] = result["score"]
    if "metadata" in result and isinstance(result["metadata"], dict):
        normalized["metadata"] = result["metadata"]
    return normalized


def normalize_reason(reason: Any) -> dict[str, Any]:
    if not isinstance(reason, dict):
        return {
            "message": str(reason),
            "requires_user_input": False,
        }
    normalized = {
        "message": str(reason.get("message") or "No reason message returned."),
        "requires_user_input": bool(reason.get("requires_user_input", False)),
    }
    for key in ["file", "line", "suggestion", "evidence"]:
        if key in reason:
            normalized[key] = reason[key]
    return normalized

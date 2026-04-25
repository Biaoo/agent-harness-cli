from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    root = Path(input_data["root"])
    task = input_data["task"]
    check = input_data["check"]
    name = check.get("name", "example_check")
    severity = check.get("severity", "error")

    reasons: list[dict[str, Any]] = []

    # Add deterministic validation here. Read paths relative to root.
    _ = root
    _ = task

    passed = not reasons
    return {
        "check": name,
        "passed": passed,
        "severity": severity,
        "summary": "Check passed." if passed else f"Found {len(reasons)} issue(s).",
        "score": 1.0 if passed else 0.0,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    with open(args.input, "r", encoding="utf-8") as handle:
        input_data = json.load(handle)
    print(json.dumps(run(input_data), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

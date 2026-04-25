from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def run_codex_checklist_judge(
    *,
    prompt: str,
    checklist_template: str,
    working_directory: str | Path,
    model: str | None = None,
    reasoning_effort: str = "low",
    codex_command: str = "codex",
    timeout_seconds: float = 180.0,
) -> str:
    """Run local `codex exec` and ask it to return a filled Markdown checklist.

    This intentionally avoids asking the model for strict JSON. Check scripts can
    parse the returned checklist deterministically and then emit harness JSON.
    """
    root = Path(working_directory).resolve()
    with tempfile.TemporaryDirectory(prefix="agent-harness-codex-checklist-") as temp_dir:
        output_path = Path(temp_dir) / "filled-checklist.md"
        full_prompt = f"""
{prompt}

Fill out this Markdown checklist. Return only the completed checklist.
Do not return JSON. Do not add prose before or after the checklist.
Use "- [x]" for satisfied items and "- [ ]" for unsatisfied items.
For each unsatisfied item, keep the item text and add one short "Reason:" line
and one short "Suggestion:" line underneath it.

Checklist:
{checklist_template}
""".strip()

        command = [
            codex_command,
            "exec",
            "--cd",
            str(root),
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(output_path),
            "--color",
            "never",
            "-c",
            'approval_policy="never"',
            "-c",
            "codex_hooks=false",
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
        ]
        if model:
            command += ["--model", model]
        command.append("-")

        completed = subprocess.run(
            command,
            input=full_prompt,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "local codex checklist judge failed "
                f"(exit {completed.returncode}): {completed.stderr[-1000:] or completed.stdout[-1000:]}"
            )
        return output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout

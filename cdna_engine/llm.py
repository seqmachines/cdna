from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

from cdna_engine.paths import REPO_ROOT


def complete_text(model: str, system: str, prompt: str) -> str:
    """Call the repo's Node AI SDK bridge lazily so non-LLM commands stay importable."""
    test_response = os.environ.get("CDNA_TEST_LLM_RESPONSE")
    if test_response is not None:
        return test_response

    script = REPO_ROOT / "scripts" / "ai_sdk_complete.mjs"
    if not script.exists():
        raise RuntimeError(f"AI SDK bridge script is missing: {script}")

    payload = json.dumps({"model": model, "system": system, "prompt": prompt}, ensure_ascii=False)
    timeout = int(os.environ.get("CDNA_LLM_TIMEOUT_SECONDS", "300"))
    try:
        completed = subprocess.run(
            ["node", str(script)],
            input=payload,
            text=True,
            capture_output=True,
            cwd=REPO_ROOT,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Node.js is required for model calls through the AI SDK bridge.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Model call timed out after {timeout} seconds") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"AI SDK model call failed: {detail}")

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI SDK bridge returned non-JSON output: {completed.stdout[:500]}") from exc

    content = parsed.get("text")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("AI SDK bridge returned an empty response")
    return content


def complete_with_codex(
    prompt: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    working_directory: str | Path | None = None,
    sandbox_mode: str = "read-only",
    additional_directories: Iterable[str | Path] | None = None,
) -> str:
    """Call Codex SDK for agentic chunk review."""
    test_response = os.environ.get("CDNA_TEST_CODEX_RESPONSE")
    if test_response is not None:
        return test_response

    script = REPO_ROOT / "scripts" / "codex_oligo_linker.mjs"
    sdk_path = REPO_ROOT / "node_modules" / "@openai" / "codex-sdk" / "dist" / "index.js"
    if not script.exists():
        raise RuntimeError(f"Codex bridge script is missing: {script}")
    if not sdk_path.exists():
        raise RuntimeError("Codex SDK is not installed. Run `npm install` in the repo.")

    payload = json.dumps(
        {
            "prompt": prompt,
            "model": model or "",
            "reasoning_effort": reasoning_effort or "",
            "sdk_path": str(sdk_path),
            "working_directory": str(Path(working_directory).expanduser().resolve()) if working_directory else str(REPO_ROOT),
            "sandbox_mode": sandbox_mode,
            "additional_directories": sorted({str(Path(path).expanduser().resolve()) for path in additional_directories or []}),
        },
        ensure_ascii=False,
    )
    timeout = int(os.environ.get("CDNA_CODEX_TIMEOUT_SECONDS", "600"))
    try:
        completed = subprocess.run(
            ["node", str(script)],
            input=payload,
            text=True,
            capture_output=True,
            cwd=REPO_ROOT,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Node.js is required for Codex linker calls.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex linker call timed out after {timeout} seconds") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Codex linker call failed: {detail}")

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Codex bridge returned non-JSON output: {completed.stdout[:500]}") from exc

    content = parsed.get("text")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Codex bridge returned an empty response")
    return content

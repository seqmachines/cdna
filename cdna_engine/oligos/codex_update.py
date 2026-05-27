from __future__ import annotations

import json
import re
import ast
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from cdna_engine.env import load_env_local
from cdna_engine.paths import REPO_ROOT

from .benchmark import (
    extract_name_guided_deterministic,
    grouped_protocol_text,
    load_candidates,
)
from .curate import DEFAULT_MODEL


DNA_LITERAL_RE = re.compile(r"\b[ACGTURYSWKMBDHVN]{12,}\b", flags=re.IGNORECASE)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _step(message: str) -> None:
    print(f"cDNA Codex v1: {message}", flush=True)


def unique_candidate_names(candidates: list[dict[str, Any]]) -> list[str]:
    by_normalized: dict[str, str] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in ("fetch_name", "display_name", "fetch_id", "canonical_id"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                normalized = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
                by_normalized.setdefault(normalized, value.strip())
                break
        for alias in item.get("aliases") or []:
            if isinstance(alias, str) and alias.strip():
                normalized = " ".join(alias.lower().replace("_", " ").replace("-", " ").split())
                by_normalized.setdefault(normalized, alias.strip())
    return sorted(by_normalized.values(), key=lambda value: value.lower())


def master_inventory_script_path(repo_root: str | Path = REPO_ROOT) -> Path:
    return Path(repo_root) / "cdna_engine" / "oligos" / "sequence_inventory.py"


def codex_sdk_version(repo_root: str | Path = REPO_ROOT) -> str | None:
    package_json = Path(repo_root) / "node_modules" / "@openai" / "codex-sdk" / "package.json"
    if not package_json.exists():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    version = data.get("version")
    return str(version) if version else None


def build_codex_prompt(
    *,
    protocol_slug: str,
    inputs: list[Path],
    candidates: list[dict[str, Any]] | None = None,
    repo_root: str | Path = REPO_ROOT,
) -> str:
    repo = Path(repo_root)
    candidates = candidates or []
    master_script = master_inventory_script_path(repo).read_text(encoding="utf-8")
    candidate_names = unique_candidate_names(candidates)
    if candidate_names:
        candidate_guidance = (
            "Unique possible adapter/primer/oligo names from training protocols "
            "are provided below. Use them only to improve generic label and role detection."
        )
        candidate_block = json.dumps(candidate_names, indent=2, ensure_ascii=False)[:50000]
    else:
        candidate_guidance = (
            "No candidate ID/name list is provided for this run. Infer adapter, primer, "
            "and oligo labels from the target protocol text itself, then make the script "
            "extract exact sequence text near those labels."
        )
        candidate_block = "[]"
    target_sources = [
        {
            "protocol_slug": protocol_slug,
            "source_names": [path.name for path in inputs],
            "text": grouped_protocol_text(inputs)[:90000],
        }
    ]
    return textwrap.dedent(
        f"""
        You are generating a run-local cDNA deterministic adapter/primer/oligo inventory script.

        Goal:
        Produce one complete Python script based on cDNA's master sequence inventory module.
        cDNA will save this script as sequence_inventory.generated.py and use it only for this protocol run.

        Inputs:
        - {candidate_guidance}
        - Target protocol text for the protocol currently being extracted. This is source text only, not ground truth.
        - cDNA's current master sequence inventory module.

        Hard constraints:
        - Return ONLY complete Python source code for sequence_inventory.generated.py.
        - The script MUST define extract_sequence_inventory(text, *, use_known_inventory=True).
        - Do NOT hardcode any literal sequence strings into code.
        - Do NOT hardcode expected candidate IDs, canonical IDs, or output records.
        - Do NOT add or infer any expected output sequences.
        - Keep final sequence strings sourced only from protocol text.
        - You MAY add protocol-specific label terms, name-hint extraction, table/header parsing, and sequence-window heuristics learned from the target protocol text.
        - Prefer table-aware extraction, oligo-family labels, and generalized sequence_template annotations for plate/table series.
        - You MAY use unique training names only to improve generic label/role terms and name hints.
        - Do NOT write files, call subprocesses, access the network, or require external packages.

        Output format:
        - Return the Python script only.
        - Do not wrap it in Markdown fences.
        - Do not include explanations.

        Unique possible adapter/primer/oligo names:
        {candidate_block}

        Target protocol text for this run:
        {json.dumps(target_sources, indent=2, ensure_ascii=False)[:90000]}

        Master cdna_engine/oligos/sequence_inventory.py:
        ```python
        {master_script[:90000]}
        ```
        """
    ).strip() + "\n"


UNSAFE_IMPORT_ROOTS = {
    "http",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "urllib",
}
UNSAFE_CALL_NAMES = {"eval", "exec", "compile", "__import__"}
UNSAFE_ATTR_NAMES = {
    "remove",
    "rmdir",
    "unlink",
    "write",
    "write_bytes",
    "write_text",
}


def _extract_python_source(raw_text: str) -> str:
    fence_match = re.search(r"```(?:python|py)?\s*\n?([\s\S]*?)\n?\s*```", raw_text)
    if fence_match:
        return fence_match.group(1).strip() + "\n"
    return raw_text.strip() + "\n"


def _looks_like_hardcoded_sequence(value: str) -> str | None:
    for match in DNA_LITERAL_RE.finditer(value):
        literal = match.group(0)
        alphabet = set(literal.upper())
        if len(literal) >= 12 and alphabet <= set("ACGTUN"):
            return literal
    return None


def validate_generated_script(script_text: str) -> dict[str, Any]:
    violations: list[str] = []
    try:
        tree = ast.parse(script_text)
    except SyntaxError as exc:
        return {"ok": False, "violations": [f"syntax error: {exc}"]}

    function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    if "extract_sequence_inventory" not in function_names:
        violations.append("missing extract_sequence_inventory function")

    id_like_assignments = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                root = name.split(".", 1)[0]
                if root in UNSAFE_IMPORT_ROOTS:
                    violations.append(f"unsafe import: {name}")

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in UNSAFE_CALL_NAMES:
                violations.append(f"unsafe call: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in UNSAFE_ATTR_NAMES:
                violations.append(f"unsafe file mutation call: {func.attr}")

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literal = _looks_like_hardcoded_sequence(node.value)
            if literal:
                violations.append(f"DNA-like literal in generated script: {literal[:32]}")
            if any(key in node.value for key in ("canonical_id", "fetch_id", "output_id")):
                id_like_assignments += 1

    if id_like_assignments > 10:
        violations.append("generated script appears to contain a hardcoded ID map")

    return {"ok": not violations, "violations": violations}


def _run_codex_sdk(
    prompt_path: Path,
    response_path: Path,
    out_dir: Path,
    repo_root: Path,
    *,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
) -> None:
    out_dir = out_dir.resolve()
    prompt_path = prompt_path.resolve()
    response_path = response_path.resolve()
    sdk_path = (repo_root / "node_modules" / "@openai" / "codex-sdk" / "dist" / "index.js").resolve()
    node_script = out_dir / "run_codex_sdk.mjs"
    node_script.write_text(
        textwrap.dedent(
            """
            import { readFile, writeFile } from "node:fs/promises";
            import { pathToFileURL } from "node:url";

            const [promptPath, responsePath, sdkPath, codexModel, codexReasoningEffort] = process.argv.slice(2);
            const prompt = await readFile(promptPath, "utf8");
            const { Codex } = await import(pathToFileURL(sdkPath).href);
            const codex = new Codex({
              apiKey: process.env.CODEX_API_KEY ?? process.env.OPENAI_API_KEY,
            });
            const threadOptions = {};
            if (codexModel) {
              threadOptions.model = codexModel;
            }
            if (codexReasoningEffort) {
              threadOptions.modelReasoningEffort = codexReasoningEffort;
            }
            const thread = codex.startThread(threadOptions);
            const turn = await thread.run(prompt);
            await writeFile(responsePath, turn.finalResponse ?? String(turn), "utf8");
            """
        ).strip() + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "node",
            str(node_script),
            str(prompt_path),
            str(response_path),
            str(sdk_path),
            codex_model or "",
            codex_reasoning_effort or "",
        ],
        check=False,
        cwd=out_dir,
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode:
        details = (completed.stderr or completed.stdout or "").strip()
        error_path = out_dir / "codex_error.raw.txt"
        if details:
            error_path.write_text(details + "\n", encoding="utf-8")
        hint = ""
        if "model_not_found" in details or "does not exist" in details:
            hint = (
                "\nCodex model hint: the requested model is not available. "
                "Use a model available to your account, for example "
                "`--codex-model gpt-5.5`, `--codex-model gpt-5.4`, "
                "`--codex-model gpt-5.3-codex`, "
                "or omit `--codex-model` to use the Codex CLI default."
            )
        elif completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        raise RuntimeError(
            f"Codex SDK command failed with exit code {completed.returncode}. "
            f"Raw error: {error_path}{hint}"
        )


def run_codex_updated_extraction(
    *,
    protocol_slug: str,
    inputs: list[Path],
    candidate_json: str | Path | None,
    output: str | Path,
    artifacts_dir: str | Path | None,
    codex_out: str | Path,
    repo_root: str | Path | None = None,
    apply_to_repo: bool = False,
    dry_run: bool = False,
    deterministic_only: bool = False,
    model: str | None = None,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root or REPO_ROOT).resolve()
    load_env_local(repo / ".env.local")
    out_dir = Path(codex_out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if apply_to_repo:
        result = {
            "schema_version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "protocol_slug": protocol_slug,
            "apply_to_repo": apply_to_repo,
            "dry_run": dry_run,
            "status": "apply_to_repo_unsupported",
            "error": "Codex v1 now writes a run-local generated script and never patches the cDNA repo.",
        }
        _write_json(out_dir / "result.json", result)
        return result

    candidates = load_candidates(candidate_json) if candidate_json else []
    _step("preparing Codex prompt and run directory")
    prompt = build_codex_prompt(
        protocol_slug=protocol_slug,
        inputs=inputs,
        candidates=candidates,
        repo_root=repo,
    )
    prompt_path = out_dir / "codex_prompt.md"
    raw_response_path = out_dir / "codex_response.raw.txt"
    generated_script_path = out_dir / "sequence_inventory.generated.py"
    validation_path = out_dir / "script_validation.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    _step(f"wrote Codex prompt: {prompt_path}")
    sdk_version = codex_sdk_version(repo)
    codex_model_label = codex_model or "sdk_default"
    codex_reasoning_effort_label = codex_reasoning_effort or "sdk_default"
    _step(
        f"Codex SDK: @openai/codex-sdk {sdk_version or 'unknown'}; "
        f"model: {codex_model_label}; reasoning effort: {codex_reasoning_effort_label}"
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol_slug": protocol_slug,
        "candidate_name_count": len(unique_candidate_names(candidates)),
        "codex_sdk_package": "@openai/codex-sdk",
        "codex_sdk_version": sdk_version,
        "codex_model": codex_model_label,
        "codex_reasoning_effort": codex_reasoning_effort_label,
        "audit_model": model or DEFAULT_MODEL,
        "prompt": str(prompt_path),
        "raw_response": str(raw_response_path),
        "generated_script": str(generated_script_path),
        "script_validation": str(validation_path),
        "apply_to_repo": apply_to_repo,
        "dry_run": dry_run,
        "status": "prompt_written",
    }
    if dry_run:
        _write_json(out_dir / "result.json", result)
        _step("dry run complete; skipping Codex SDK call and extraction")
        return result

    try:
        _step("calling Codex SDK to generate a run-local inventory script")
        _run_codex_sdk(
            prompt_path,
            raw_response_path,
            out_dir,
            repo,
            codex_model=codex_model,
            codex_reasoning_effort=codex_reasoning_effort,
        )
    except (RuntimeError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        result["status"] = "codex_sdk_failed"
        result["error"] = str(exc)
        _write_json(out_dir / "result.json", result)
        _step(f"Codex SDK failed: {exc}")
        return result

    _step(f"Codex response written: {raw_response_path}")
    raw_response = raw_response_path.read_text(encoding="utf-8")
    generated_script = _extract_python_source(raw_response)
    generated_script_path.write_text(generated_script, encoding="utf-8")
    _step(f"generated inventory script written: {generated_script_path}")
    _step("validating generated script")
    validation = validate_generated_script(generated_script)
    _write_json(validation_path, validation)
    if not validation["ok"]:
        result["status"] = "invalid_generated_script"
        result["error"] = "Generated script failed validation."
        result["validation_violations"] = validation["violations"]
        _write_json(out_dir / "result.json", result)
        _step(f"generated script failed validation: {validation_path}")
        return result

    _step("extracting deterministic inventory with generated script")
    extraction = extract_name_guided_deterministic(
        protocol_slug=protocol_slug,
        inputs=inputs,
        candidates=candidates,
        output=Path(output),
        artifacts_dir=Path(artifacts_dir) if artifacts_dir else out_dir,
        deterministic_only=deterministic_only or bool(candidates),
        model=model or DEFAULT_MODEL,
        inventory_script=generated_script_path,
    )
    result["status"] = "generated_script_executed"
    result["prediction_output"] = str(output)
    rows = (extraction.get("protocol") or {}).get("adapter_primer_sequences") or []
    result["prediction_record_count"] = len(rows)
    audit = extraction.get("audit") or {}
    result["audit_status"] = audit.get("audit_status")
    result["audit_annotation_count"] = len(audit.get("inventory_annotations") or audit.get("annotations") or [])
    _write_json(out_dir / "result.json", result)
    _step(f"Codex v1 result written: {out_dir / 'result.json'}")
    return result

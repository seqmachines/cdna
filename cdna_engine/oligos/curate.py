from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from cdna_engine.env import load_env_local
from cdna_engine.io import output_slug, prepare_protocol_text
from cdna_engine.llm import complete_text
from cdna_engine.paths import OUTPUTS_DIR, REPO_ROOT, SEQUENCE_INVENTORY_DB, SCRIPTS_DIR
from cdna_engine.tsv import parse_tsv, tsv_cell, write_tsv

from .inventory import build_protocol_context, inventory_tsv, parse_audit, write_inventory_json


console = Console()

DEFAULT_MODEL = "gemini/gemini-3.1-pro-preview"
INVENTORY_COLUMNS = [
    "id",
    "name",
    "sequence",
    "role",
    "platform",
    "protocol",
    "source_url",
    "orientation",
    "modifications",
    "notes",
]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def has_blocking_audit_findings(audit: dict[str, Any]) -> bool:
    if audit.get("audit_status") != "pass":
        return True
    for key in [
        "missing_sequences",
        "suspected_regex_gaps",
        "proposed_extractor_changes",
        "proposed_inventory_rows",
    ]:
        if isinstance(audit.get(key), list) and audit[key]:
            return True
    return False


def missing_oligos_tsv(audit: dict[str, Any]) -> str:
    rows: list[dict[str, Any]] = []
    for item in audit.get("oligo_terms") or []:
        matched = item.get("matched_candidate_ids") if isinstance(item, dict) else None
        rows.append(
            {
                "kind": "oligo_term",
                "name_hint": item.get("name_hint") if isinstance(item, dict) else None,
                "sequence_text": item.get("sequence_text") if isinstance(item, dict) else None,
                "source_span": item.get("source_span") if isinstance(item, dict) else None,
                "reason": f"matched candidates: {';'.join(matched)}" if matched else "no deterministic candidate matched",
                "suggested_regex_case": "",
            }
        )
    for item in audit.get("missing_sequences") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "kind": "missing_sequence",
                "name_hint": item.get("name_hint"),
                "sequence_text": item.get("sequence_text"),
                "source_span": item.get("source_span"),
                "reason": item.get("reason_script_missed_it"),
                "suggested_regex_case": item.get("suggested_regex_case"),
            }
        )
    return write_tsv(
        ["kind", "name_hint", "sequence_text", "source_span", "reason", "suggested_regex_case"],
        rows,
    )


def pending_inventory_tsv(audit: dict[str, Any], inventory: dict[str, Any]) -> str:
    allowed_sequences = {
        candidate.get("sequence")
        for candidate in inventory.get("candidates") or []
        if candidate.get("sequence")
    }
    rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(audit.get("proposed_inventory_rows") or [], start=1):
        if not isinstance(raw_row, dict):
            continue
        proposed_sequence = raw_row.get("sequence")
        sequence_allowed = isinstance(proposed_sequence, str) and proposed_sequence in allowed_sequences
        name = raw_row.get("name") if isinstance(raw_row.get("name"), str) else ""
        fallback_id = "_".join(name.lower().split()) if name else f"pending_oligo_{index}"
        notes = " ".join(
            str(item)
            for item in [
                raw_row.get("notes"),
                (
                    "LLM-proposed sequence was ignored because it was not present in deterministic candidates."
                    if proposed_sequence and not sequence_allowed
                    else ""
                ),
            ]
            if item
        )
        rows.append(
            {
                "id": raw_row.get("id") or fallback_id,
                "name": name,
                "sequence": proposed_sequence if sequence_allowed else "",
                "role": raw_row.get("role"),
                "platform": raw_row.get("platform"),
                "protocol": raw_row.get("protocol"),
                "source_url": raw_row.get("source_url"),
                "orientation": raw_row.get("orientation"),
                "modifications": raw_row.get("modifications"),
                "notes": notes,
                "status": "pending_review",
            }
        )
    return write_tsv([*INVENTORY_COLUMNS, "status"], rows)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()
    return stripped + "\n"


def run_audit(context: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
    raw = complete_text(
        model=model,
        system=(
            "You are a strict sequencing protocol sequence-inventory auditor. "
            "Return only valid JSON. Never generate, rewrite, normalize, repair, "
            "complete, reverse-complement, or otherwise modify sequence strings."
        ),
        prompt=context["audit_prompt"],
    )
    return raw, parse_audit(raw)


def propose_extractor_patch(audit: dict[str, Any], protocol_text: str, model: str) -> str:
    extractor_source = (SCRIPTS_DIR / "sequence_inventory.py").read_text(encoding="utf-8")
    evidence_items = [
        *(audit.get("oligo_terms") or []),
        *(audit.get("missing_sequences") or []),
    ]
    evidence = "\n".join(
        str(item.get("source_span") or item.get("reason_script_missed_it") or "")
        for item in evidence_items
        if isinstance(item, dict)
    )
    prompt = f"""Create a proposed patch for the deterministic oligo extractor.

Return ONLY one of:
1. A git unified diff beginning with "diff --git a/scripts/sequence_inventory.py b/scripts/sequence_inventory.py"
2. The exact string NO_PATCH if there is no safe generic deterministic extractor change.

Rules:
- Do not apply the patch.
- Do not hard-code protocol-specific rows or sequence strings.
- Do not generate, rewrite, reverse-complement, normalize, complete, or repair oligo sequences.
- Only improve deterministic extraction logic using generic parsing rules supported by the audit evidence.
- Keep the patch limited to scripts/sequence_inventory.py.
- Preserve existing tests and behavior for false positives.

Audit JSON:
{json.dumps(audit, indent=2, ensure_ascii=False)}

Relevant protocol evidence:
{evidence or "(none)"}

Protocol text excerpt:
{protocol_text[:25000]}

Current scripts/sequence_inventory.py:
{extractor_source}"""
    raw = complete_text(
        model=model,
        system=(
            "You are a careful Python maintainer writing review-only deterministic "
            "extractor patches. Return only a git unified diff or NO_PATCH."
        ),
        prompt=prompt,
    )
    return _strip_code_fence(raw)


def write_review_readme(run_dir: Path, state: dict[str, Any], message: str) -> None:
    display_run_dir = display_path(run_dir)
    resume_command = f"cdna curate oligos resume --run-dir {display_run_dir}"
    text = f"""# Oligo Curation Review

{message}

## State

```json
{json.dumps(state, indent=2, ensure_ascii=False)}
```

## Approval files

- Review the latest `iteration-N.proposed-extractor.patch`; if approved, save it as `approved-extractor.patch`.
- Review the latest `iteration-N.pending-inventory.tsv`; if approved, save reviewed rows as `approved-inventory.tsv`.
- Resume with:

```bash
{resume_command}
```
"""
    (run_dir / "README.md").write_text(text, encoding="utf-8")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    _write_json(run_dir / "state.json", state)


def write_iteration_artifacts(
    run_dir: Path,
    iteration: int,
    protocol_text: str,
    context: dict[str, Any],
    raw_audit: str,
    audit: dict[str, Any],
) -> None:
    prefix = run_dir / f"iteration-{iteration}"
    (prefix.with_suffix(".protocol.txt")).write_text(protocol_text, encoding="utf-8")
    write_inventory_json(prefix.with_suffix(".inventory.json"), context["inventory"])
    (prefix.with_suffix(".sequence-inventory.tsv")).write_text(
        inventory_tsv(context["inventory"]),
        encoding="utf-8",
    )
    (prefix.with_suffix(".audit.raw.txt")).write_text(raw_audit, encoding="utf-8")
    _write_json(prefix.with_suffix(".audit.json"), audit)
    (prefix.with_suffix(".missing-oligos.tsv")).write_text(missing_oligos_tsv(audit), encoding="utf-8")
    (prefix.with_suffix(".pending-inventory.tsv")).write_text(
        pending_inventory_tsv(audit, context["inventory"]),
        encoding="utf-8",
    )


def apply_approved_patch(run_dir: Path) -> bool:
    patch_path = run_dir / "approved-extractor.patch"
    if not patch_path.exists():
        return False
    patch = patch_path.read_text(encoding="utf-8")
    if not patch.strip() or patch.strip() == "NO_PATCH":
        return False
    subprocess.run(["git", "apply", str(patch_path)], cwd=REPO_ROOT, check=True)
    shutil.move(str(patch_path), run_dir / f"applied-extractor-{int(time.time())}.patch")
    return True


def apply_approved_inventory(run_dir: Path) -> int:
    approved_path = run_dir / "approved-inventory.tsv"
    if not approved_path.exists():
        return 0
    approved_rows = [
        row for row in parse_tsv(approved_path.read_text(encoding="utf-8")) if row.get("id") and row.get("sequence")
    ]
    if not approved_rows:
        return 0
    current = SEQUENCE_INVENTORY_DB.read_text(encoding="utf-8")
    current_rows = parse_tsv(current)
    seen = {f"{row.get('id')}\t{row.get('sequence')}" for row in current_rows}
    additions = [row for row in approved_rows if f"{row.get('id')}\t{row.get('sequence')}" not in seen]
    if not additions:
        return 0
    body = current if current.endswith("\n") else current + "\n"
    addition_text = "\n".join(
        "\t".join(tsv_cell(row.get(column)) for column in INVENTORY_COLUMNS) for row in additions
    )
    SEQUENCE_INVENTORY_DB.write_text(body + addition_text + "\n", encoding="utf-8")
    shutil.move(str(approved_path), run_dir / f"applied-inventory-{int(time.time())}.tsv")
    return len(additions)


def run_curation(
    input_path: Path,
    model: str = DEFAULT_MODEL,
    max_iterations: int = 5,
    run_dir: Path | None = None,
    start_iteration: int = 1,
) -> Path:
    load_env_local()
    input_path = input_path.expanduser().resolve()
    protocol_text = prepare_protocol_text(input_path)
    run_dir = run_dir or OUTPUTS_DIR / "curation" / output_slug(input_path)
    run_dir.mkdir(parents=True, exist_ok=True)

    state: dict[str, Any] = {
        "input": str(input_path),
        "model": model,
        "max_iterations": max_iterations,
        "status": "running",
        "last_iteration": start_iteration - 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    for iteration in range(start_iteration, max_iterations + 1):
        console.print(f"Running oligo curation iteration {iteration}...")
        context = build_protocol_context(protocol_text)
        raw_audit, audit = run_audit(context, model)
        write_iteration_artifacts(run_dir, iteration, protocol_text, context, raw_audit, audit)

        state.update(
            {
                "status": "awaiting_human_approval" if has_blocking_audit_findings(audit) else "pass",
                "last_iteration": iteration,
                "candidate_count": len(context["inventory"].get("candidates") or []),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

        if not has_blocking_audit_findings(audit):
            save_state(run_dir, state)
            write_review_readme(run_dir, state, "LLM audit passed; no blocking missing oligos were reported.")
            console.print(f"Audit passed. Artifacts: {display_path(run_dir)}")
            return run_dir

        patch = propose_extractor_patch(audit, protocol_text, model)
        (run_dir / f"iteration-{iteration}.proposed-extractor.patch").write_text(patch, encoding="utf-8")
        save_state(run_dir, state)
        write_review_readme(
            run_dir,
            state,
            "Audit found missing or uncertain oligo evidence. Review proposed patch/DB rows before resuming.",
        )
        console.print(f"Awaiting review. Artifacts: {display_path(run_dir)}")
        console.print(f"Resume with: cdna curate oligos resume --run-dir {display_path(run_dir)}")
        return run_dir

    state.update(
        {
            "status": "max_iterations_reached",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    save_state(run_dir, state)
    write_review_readme(run_dir, state, "Maximum iterations reached before audit pass.")
    return run_dir


def resume_curation(
    run_dir: Path,
    model: str | None = None,
    max_iterations: int | None = None,
) -> Path:
    load_env_local()
    run_dir = run_dir.expanduser().resolve()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    patch_applied = apply_approved_patch(run_dir)
    inventory_rows_applied = apply_approved_inventory(run_dir)
    _write_json(
        run_dir / "approved-apply-log.json",
        {
            "patch_applied": patch_applied,
            "inventory_rows_applied": inventory_rows_applied,
            "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return run_curation(
        input_path=Path(state["input"]),
        model=model or state.get("model") or DEFAULT_MODEL,
        max_iterations=max_iterations or int(state.get("max_iterations") or 5),
        run_dir=run_dir,
        start_iteration=int(state.get("last_iteration") or 0) + 1,
    )

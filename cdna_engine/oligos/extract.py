from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from cdna_engine.env import load_env_local
from cdna_engine.io import output_slug, prepare_protocol_text
from cdna_engine.paths import OUTPUTS_DIR, REPO_ROOT, SEQUENCE_INVENTORY_DB
from cdna_engine.tsv import tsv_cell, write_tsv

from .curate import DEFAULT_MODEL, has_blocking_audit_findings, missing_oligos_tsv, propose_extractor_patch, run_audit
from .inventory import build_protocol_context, extract_sequence_inventory, inventory_tsv, write_inventory_json


FINAL_OLIGO_COLUMNS = [
    "name",
    "role",
    "sequence",
    "orientation",
    "modifications",
    "source",
    "inventory_id",
    "source_span_ids",
    "confidence",
    "review_status",
    "review_note",
    "uncertainty",
]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _candidate_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _candidate_strings(value: Any) -> list[str]:
    return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def _candidate_number(value: Any) -> float | None:
    return min(1.0, max(0.0, value)) if isinstance(value, (int, float)) else None


def _candidate_orientation(value: Any) -> str:
    return value if value in {"5_to_3", "3_to_5", "unknown"} else "unknown"


def _audit_review_map(audit: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    reviews: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for raw in audit.get("candidate_reviews") or []:
        if not isinstance(raw, dict):
            continue
        if _candidate_string(raw.get("sequence")):
            warnings.append(
                "Ignored LLM-proposed sequence in candidate review for "
                f"{raw.get('candidate_id') or raw.get('source_span_id') or 'unknown candidate'}."
            )
        parsed = {
            "decision": raw.get("decision") if raw.get("decision") in {"accept", "reject", "review"} else None,
            "confidence": _candidate_number(raw.get("confidence")),
            "suggested_name": _candidate_string(raw.get("suggested_name")),
            "suggested_role": _candidate_string(raw.get("suggested_role")),
            "reason": _candidate_string(raw.get("reason")),
        }
        for key in [_candidate_string(raw.get("candidate_id")), _candidate_string(raw.get("source_span_id"))]:
            if key:
                reviews[key] = parsed
    return reviews, warnings


def _stripped_sequence_letters(sequence: str) -> str:
    import re

    sequence = re.sub(r"\[[^\]]+\]", "", sequence)
    sequence = re.sub(r"N\d+", "", sequence, flags=re.I)
    sequence = re.sub(r"[rR][ACGTUacgtu]", "", sequence)
    return re.sub(r"[^A-Za-z]", "", sequence)


def _has_unexpected_lowercase(sequence: str) -> bool:
    import re

    without_placeholders = re.sub(r"\[[^\]]+\]", "", sequence)
    without_rna_mods = re.sub(r"[rR][ACGTUacgtu]", "", without_placeholders)
    return bool(re.search(r"[a-z]", without_rna_mods))


def _hard_reject_candidate(candidate: dict[str, Any], llm_accepted: bool = False) -> str | None:
    import re

    if candidate.get("source") == "known_inventory":
        return None
    sequence = _candidate_string(candidate.get("sequence")) or ""
    letters = _stripped_sequence_letters(sequence)
    source_text = _candidate_string(candidate.get("source_text")) or ""
    has_placeholder = bool(re.search(r"\[[^\]]+\]|N\d+", sequence, flags=re.I))
    has_orientation = _candidate_orientation(candidate.get("orientation_hint")) != "unknown"
    has_strong_bases = bool(re.search(r"[ACGT]{8,}", letters))
    has_label = bool(
        re.search(r"\b(adapter|adaptor|primer|oligo|index|read\s*[12]|truseq|p5|p7|tso|bead|barcode)\b", source_text, flags=re.I)
    )
    if _has_unexpected_lowercase(sequence):
        return "Rejected: lowercase English-like text matched the permissive IUPAC fallback."
    if len(letters) < 10 and not has_placeholder:
        return "Rejected: candidate is too short without an explicit variable-region placeholder."
    if not has_strong_bases and not has_placeholder:
        return "Rejected: candidate lacks a convincing A/C/G/T sequence core."
    if not llm_accepted and candidate.get("source") == "regex" and not has_orientation and not has_label and not has_placeholder:
        return "Rejected: regex fallback hit has no strong oligo context."
    return None


def _clean_candidate_name(name: str | None, index: int) -> str:
    import re

    if not name:
        return f"Unlabeled oligo candidate {index + 1}"
    trimmed = name.strip()
    if re.match(r"^[35]['’′]?$", trimmed):
        return f"Unlabeled oligo candidate {index + 1}"
    if len(trimmed) > 100 and re.search(r"[ACGT]{12,}", trimmed):
        return f"Unlabeled oligo candidate {index + 1}"
    return trimmed


def inventory_to_final_oligos(inventory: dict[str, Any], audit: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    reviews, warnings = _audit_review_map(audit)
    raw_oligos: list[dict[str, Any]] = []
    for index, candidate in enumerate(inventory.get("candidates") or []):
        candidate_id = _candidate_string(candidate.get("id"))
        span_id = _candidate_string(candidate.get("source_span_id"))
        review = (reviews.get(candidate_id or "") or reviews.get(span_id or "")) if (candidate_id or span_id) else None
        hard_reject = _hard_reject_candidate(candidate, review and review.get("decision") == "accept")
        if hard_reject or (review and review.get("decision") == "reject"):
            continue
        review_status = (
            "accepted"
            if review and review.get("decision") == "accept"
            else "needs_review"
            if review and review.get("decision") == "review"
            else "accepted_by_rules"
        )
        raw_source = _candidate_string(candidate.get("source"))
        raw_oligos.append(
            {
                "name": _clean_candidate_name(
                    (review or {}).get("suggested_name") or _candidate_string(candidate.get("name_hint")),
                    index,
                ),
                "role": (review or {}).get("suggested_role") or _candidate_string(candidate.get("role_hint")),
                "sequence": _candidate_string(candidate.get("sequence")),
                "orientation": _candidate_orientation(candidate.get("orientation_hint")),
                "modifications": _candidate_strings(candidate.get("modifications")),
                "source": "known_inventory" if raw_source == "known_inventory" else "deterministic",
                "inventory_id": _candidate_string(candidate.get("inventory_id")),
                "source_span_ids": [span_id] if span_id else [],
                "confidence": (review or {}).get("confidence"),
                "review_status": review_status,
                "review_note": (review or {}).get("reason")
                or (
                    "Passed deterministic final-output filters; no LLM candidate confidence was available."
                    if review_status == "accepted_by_rules"
                    else None
                ),
                "uncertainty": (
                    "LLM audit marked this candidate for review."
                    if review_status == "needs_review"
                    else "Detected by deterministic sequence-pattern fallback; verify name and role."
                    if raw_source == "regex" and not review
                    else None
                ),
            }
        )
    return _dedupe_final_oligos(raw_oligos), warnings


def _dedupe_final_oligos(oligos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for oligo in oligos:
        key = "|".join(
            [
                oligo.get("sequence") or "",
                oligo.get("role") or "",
                oligo.get("orientation") or "",
                oligo.get("inventory_id") or "",
            ]
        )
        current = by_key.get(key)
        if current is None:
            by_key[key] = dict(oligo)
            continue
        current["source_span_ids"] = sorted(set(current.get("source_span_ids") or []) | set(oligo.get("source_span_ids") or []))
        if not current.get("confidence") or ((oligo.get("confidence") or 0) > current.get("confidence")):
            current["confidence"] = oligo.get("confidence")
        if current.get("review_status") != "accepted" and oligo.get("review_status") == "accepted":
            current["review_status"] = oligo.get("review_status")
            current["review_note"] = oligo.get("review_note")
    return list(by_key.values())


def final_oligos_tsv(oligos: list[dict[str, Any]]) -> str:
    return write_tsv(FINAL_OLIGO_COLUMNS, oligos)


def _artifact_paths(input_path: Path, output: Path | None, artifacts_dir: Path | None) -> dict[str, Path]:
    slug = output_slug(input_path)
    out_dir = artifacts_dir or (output.parent if output else OUTPUTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_oligo_tsv = output or out_dir / f"{slug}.final-oligos.tsv"
    base = final_oligo_tsv.name.removesuffix(".final-oligos.tsv").removesuffix(".tsv")
    return {
        "protocol_text": out_dir / f"{base}.protocol.txt",
        "initial_inventory_json": out_dir / f"{base}.initial.inventory.json",
        "initial_inventory_tsv": out_dir / f"{base}.initial.sequence-inventory.tsv",
        "audit_raw": out_dir / f"{base}.audit.raw.txt",
        "audit_json": out_dir / f"{base}.audit.json",
        "missing_oligos_tsv": out_dir / f"{base}.missing-oligos.tsv",
        "proposed_patch": out_dir / f"{base}.proposed-extractor.patch",
        "final_inventory_json": out_dir / f"{base}.final.inventory.json",
        "final_inventory_tsv": out_dir / f"{base}.final.sequence-inventory.tsv",
        "final_oligo_tsv": final_oligo_tsv,
        "final_json": out_dir / f"{base}.extract.json",
    }


def _load_temp_extractor(path: Path):
    spec = importlib.util.spec_from_file_location("cdna_temp_sequence_inventory", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load temporary extractor: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_with_temporary_patch(text: str, patch: str) -> tuple[dict[str, Any] | None, str | None]:
    patch_text = patch.strip()
    if not patch_text or patch_text == "NO_PATCH":
        return None, None
    patch_text = patch_text.replace("a/scripts/sequence_inventory.py", "a/cdna_engine/oligos/sequence_inventory.py")
    patch_text = patch_text.replace("b/scripts/sequence_inventory.py", "b/cdna_engine/oligos/sequence_inventory.py")
    patch_text = patch_text.replace("--- scripts/sequence_inventory.py", "--- cdna_engine/oligos/sequence_inventory.py")
    patch_text = patch_text.replace("+++ scripts/sequence_inventory.py", "+++ cdna_engine/oligos/sequence_inventory.py")
    with tempfile.TemporaryDirectory(prefix="cdna-extractor-") as tmp:
        tmp_root = Path(tmp)
        temp_module_dir = tmp_root / "cdna_engine" / "oligos"
        temp_module_dir.mkdir(parents=True)
        (tmp_root / "cdna_engine" / "__init__.py").write_text("", encoding="utf-8")
        (temp_module_dir / "__init__.py").write_text("", encoding="utf-8")
        (tmp_root / "data" / "sequence_inventory").mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / "cdna_engine" / "oligos" / "sequence_inventory.py", temp_module_dir / "sequence_inventory.py")
        shutil.copyfile(SEQUENCE_INVENTORY_DB, tmp_root / "data" / "sequence_inventory" / "oligos.tsv")
        patch_path = tmp_root / "temporary-extractor.patch"
        patch_path.write_text(patch_text + "\n", encoding="utf-8")
        try:
            subprocess.run(["git", "apply", str(patch_path)], cwd=tmp_root, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            return None, (exc.stderr or exc.stdout or str(exc)).strip()
        module = _load_temp_extractor(temp_module_dir / "sequence_inventory.py")
        return module.extract_sequence_inventory(text), None


def extract_oligos_customer(
    input_path: Path,
    model: str = DEFAULT_MODEL,
    output: Path | None = None,
    artifacts_dir: Path | None = None,
    deterministic_only: bool = False,
) -> dict[str, Any]:
    load_env_local()
    input_path = input_path.expanduser().resolve()
    protocol_text = prepare_protocol_text(input_path)
    paths = _artifact_paths(input_path, output, artifacts_dir)
    paths["protocol_text"].write_text(protocol_text, encoding="utf-8")

    initial_inventory = extract_sequence_inventory(protocol_text)
    write_inventory_json(paths["initial_inventory_json"], initial_inventory)
    paths["initial_inventory_tsv"].write_text(inventory_tsv(initial_inventory), encoding="utf-8")

    audit: dict[str, Any] = {
        "audit_status": "not_run",
        "missing_sequences": [],
        "candidate_reviews": [],
        "oligo_terms": [],
        "suspected_regex_gaps": [],
        "proposed_inventory_rows": [],
        "proposed_extractor_changes": [],
        "human_review_required": False,
    }
    raw_audit = ""
    proposed_patch = "NO_PATCH\n"
    repair_status = "not_needed"
    final_inventory = initial_inventory
    warnings: list[str] = []

    if not deterministic_only:
        context = build_protocol_context(protocol_text)
        raw_audit, audit = run_audit(context, model)
        paths["audit_raw"].write_text(raw_audit, encoding="utf-8")
        _write_json(paths["audit_json"], audit)
        paths["missing_oligos_tsv"].write_text(missing_oligos_tsv(audit), encoding="utf-8")
        if has_blocking_audit_findings(audit):
            proposed_patch = propose_extractor_patch(audit, protocol_text, model)
            paths["proposed_patch"].write_text(proposed_patch, encoding="utf-8")
            patched_inventory, patch_error = _extract_with_temporary_patch(protocol_text, proposed_patch)
            if patched_inventory is not None:
                final_inventory = patched_inventory
                repair_status = "temporary_patch_applied"
            elif patch_error:
                repair_status = "temporary_patch_failed"
                warnings.append(f"Temporary extractor patch failed: {patch_error}")
            else:
                repair_status = "no_safe_patch"
        else:
            paths["proposed_patch"].write_text(proposed_patch, encoding="utf-8")
    else:
        paths["audit_raw"].write_text("", encoding="utf-8")
        _write_json(paths["audit_json"], audit)
        paths["missing_oligos_tsv"].write_text(missing_oligos_tsv(audit), encoding="utf-8")
        paths["proposed_patch"].write_text(proposed_patch, encoding="utf-8")

    write_inventory_json(paths["final_inventory_json"], final_inventory)
    paths["final_inventory_tsv"].write_text(inventory_tsv(final_inventory), encoding="utf-8")
    final_oligos, review_warnings = inventory_to_final_oligos(final_inventory, audit)
    warnings.extend(review_warnings)
    paths["final_oligo_tsv"].write_text(final_oligos_tsv(final_oligos), encoding="utf-8")

    result = {
        "source": str(input_path),
        "model": None if deterministic_only else model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repair_status": repair_status,
        "protocol": {
            "adapter_primer_sequences": final_oligos,
            "source_spans": final_inventory.get("source_spans") or {},
            "warnings": warnings,
        },
        "audit": audit,
        "artifacts": {name: str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path) for name, path in paths.items()},
    }
    _write_json(paths["final_json"], result)
    return result

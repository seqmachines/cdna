from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from cdna_engine.env import load_env_local
from cdna_engine.io import prepare_protocol_text
from cdna_engine.llm import complete_text
from cdna_engine.paths import REPO_ROOT

from .curate import DEFAULT_MODEL
from .inventory import extract_sequence_inventory, inventory_tsv, write_inventory_json

DEFAULT_BASELINE_PROTOCOL_TEXT_LIMIT = 250_000
BASELINE_OLIGO_SKILL_PATH = REPO_ROOT / "skills" / "baseline-oligo-extraction.md"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _step(message: str) -> None:
    print(f"cDNA extraction: {message}", flush=True)


def _normalize(value: str) -> str:
    value = value.lower()
    value = value.replace("'", "")
    value = re.sub(r"([a-z])([0-9])", r"\1 \2", value)
    value = re.sub(r"([0-9])([a-z])", r"\1 \2", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _candidate_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _extract_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", text)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    return None


def load_candidates(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        raw_candidates = data.get("candidates", data.get("records", []))
    else:
        raw_candidates = data
    if not isinstance(raw_candidates, list):
        raise ValueError(f"Candidate file must contain a candidate list: {path}")
    return [item for item in raw_candidates if isinstance(item, dict)]


def grouped_protocol_text(inputs: list[Path]) -> str:
    sections: list[str] = []
    for input_path in inputs:
        resolved = input_path.expanduser().resolve()
        sections.append(
            f"[[SOURCE_FILE: {resolved.name}]]\n{prepare_protocol_text(resolved)}"
        )
    return "\n\n".join(sections)


def _candidate_terms(candidate: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ["fetch_name", "fetch_id", "display_name", "canonical_id"]:
        value = _candidate_string(candidate.get(key))
        if value:
            terms.append(value.replace("-", " ").replace("_", " "))
    for alias in candidate.get("aliases") or []:
        if isinstance(alias, str) and alias.strip():
            terms.append(alias)
    seen: set[str] = set()
    output: list[str] = []
    for term in terms:
        normalized = _normalize(term)
        if len(normalized) >= 4 and normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
    return output


def _line_window(text: str, start: int, end: int, radius: int = 800) -> str:
    window_start = max(0, start - radius)
    window_end = min(len(text), end + radius)
    return text[window_start:window_end]


def _candidate_hit_spans(text: str, candidate: dict[str, Any]) -> list[tuple[int, int]]:
    normalized_text = _normalize(text)
    spans: list[tuple[int, int]] = []
    for term in _candidate_terms(candidate):
        index = normalized_text.find(term)
        while index != -1:
            spans.append((index, index + len(term)))
            index = normalized_text.find(term, index + 1)
    return spans


def _candidate_name_found(text: str, candidate: dict[str, Any]) -> bool:
    normalized_text = _normalize(text)
    return any(term in normalized_text for term in _candidate_terms(candidate))


def _sequence_near_candidate(
    text: str,
    seq_candidate: dict[str, Any],
    expected_candidate: dict[str, Any],
) -> bool:
    context = " ".join(
        str(seq_candidate.get(key) or "")
        for key in ["name_hint", "role_hint", "source_text"]
    )
    normalized_context = _normalize(context)
    if any(term in normalized_context for term in _candidate_terms(expected_candidate)):
        return True

    start = seq_candidate.get("start")
    end = seq_candidate.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    window = _normalize(_line_window(text, start, end))
    return any(term in window for term in _candidate_terms(expected_candidate))


def _review_decisions(audit: dict[str, Any]) -> dict[str, str]:
    decisions: dict[str, str] = {}
    for item in audit.get("candidate_reviews") or []:
        if not isinstance(item, dict):
            continue
        decision = item.get("decision")
        if decision not in {"accept", "reject", "review"}:
            continue
        for key in ["candidate_id", "source_span_id"]:
            value = _candidate_string(item.get(key))
            if value:
                decisions[value] = decision
    return decisions


def _inventory_annotations(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    annotations: dict[str, dict[str, Any]] = {}
    for item in audit.get("inventory_annotations") or audit.get("annotations") or []:
        if not isinstance(item, dict):
            continue
        for key in ["source_span_id", "candidate_id", "deterministic_candidate_id", "id"]:
            value = _candidate_string(item.get(key))
            if value:
                annotations[value] = item
    return annotations


def _display_from_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _adapter_primer_row(
    protocol_slug: str,
    candidate: dict[str, Any],
    seq_candidate: dict[str, Any] | None,
    evidence: str,
    source_span_id: str | None,
    *,
    source: str,
) -> dict[str, Any]:
    sequence = _candidate_string((seq_candidate or {}).get("sequence"))
    return {
        "canonical_id": candidate.get("output_id") or candidate.get("canonical_id"),
        "display_name": candidate.get("display_name") or candidate.get("fetch_name"),
        "name": candidate.get("fetch_name") or candidate.get("display_name") or candidate.get("canonical_id"),
        "protocol_slug": protocol_slug,
        "protocol_version": candidate.get("protocol_version"),
        "record_type": candidate.get("record_type") or "oligo",
        "sequence": sequence,
        "orientation": (seq_candidate or {}).get("orientation_hint") or "unknown",
        "modifications": (seq_candidate or {}).get("modifications") or [],
        "source": source,
        "inventory_id": (seq_candidate or {}).get("inventory_id"),
        "source_span_ids": [source_span_id] if source_span_id else [],
        "evidence": evidence,
        "confidence": (seq_candidate or {}).get("confidence"),
        "review_status": "accepted_by_rules" if seq_candidate else "needs_review",
        "review_note": None if seq_candidate else "Expected candidate name was found, but no deterministic sequence candidate matched nearby.",
        "uncertainty": None if seq_candidate else "named_candidate_without_sequence",
        "deterministic_candidate_id": (seq_candidate or {}).get("id"),
    }


def _slugify(value: str) -> str:
    value = value.lower().replace("'", "")
    value = re.sub(r"([a-z])([0-9])", r"\1-\2", value)
    value = re.sub(r"([0-9])([a-z])", r"\1-\2", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-") or "unnamed"


def _record_type_from_inventory(seq_candidate: dict[str, Any]) -> str:
    context = " ".join(
        str(seq_candidate.get(key) or "")
        for key in ["name_hint", "role_hint", "source_text", "family_label"]
    ).lower()
    if "adapter" in context or "adaptor" in context:
        return "adapter"
    if any(term in context for term in ["nextera", "pcr primer", "amplification primer", "sequencing primer"]):
        return "primer"
    if any(
        term in context
        for term in [
            "round1",
            "round 1",
            "round2",
            "round 2",
            "round3",
            "round 3",
            "ligation",
            "linker",
            "blocking",
            "template switch",
            "tso",
        ]
    ):
        return "oligo"
    if any(term in context for term in ["primer", "read 1", "read1", "read 2", "read2", "pcr"]):
        return "primer"
    return "oligo"


def _clean_inventory_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"^(?:[A-H]\d{1,2}|[A-Z]{1,6}[_-]?\d{2,6})\s*[-:]\s*", "", value.strip())
    cleaned = re.sub(r"\s*\([^)]*used with[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:;,")
    return cleaned or None


def _template_count_key(family_label: str | None, sequence_template: str | None) -> tuple[str, str] | None:
    if not family_label or not sequence_template:
        return None
    return family_label, sequence_template


def _source_identity_label(
    seq_candidate: dict[str, Any],
    annotation: dict[str, Any],
    family_label: str | None,
    *,
    use_template: bool,
) -> str:
    if use_template and family_label:
        return family_label
    return (
        _candidate_string(annotation.get("semantic_name"))
        or _clean_inventory_label(_candidate_string(seq_candidate.get("name_hint")))
        or _display_from_label(family_label)
        or _candidate_string(seq_candidate.get("role_hint"))
        or _candidate_string(seq_candidate.get("id"))
        or "unnamed oligo"
    )


def _oriented_evidence(sequence: str, orientation: str | None) -> str:
    if orientation == "3_to_5":
        return f"3'- {sequence} -5'"
    return f"5'- {sequence} -3'"


def _build_open_inventory_audit_prompt(
    protocol_slug: str,
    inventory: dict[str, Any],
) -> str:
    audit_rows = []
    for item in inventory.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        audit_rows.append(
            {
                "id": item.get("id"),
                "source_span_id": item.get("source_span_id"),
                "source": item.get("source"),
                "name_hint": item.get("name_hint"),
                "role_hint": item.get("role_hint"),
                "family_label": item.get("family_label"),
                "sequence_template": item.get("sequence_template"),
                "sequence": item.get("sequence"),
                "source_text": item.get("source_text"),
            }
        )
    return f"""Audit deterministic open oligo inventory grouping for protocol {protocol_slug}.

The deterministic extractor already copied exact sequences from source text. Your job is only to annotate those rows.

Deterministic inventory rows:
{json.dumps(audit_rows[:800], indent=2, ensure_ascii=False)}

Return ONLY valid JSON with this shape:
{{
  "audit_status": "pass",
  "inventory_annotations": [
    {{
      "source_span_id": "seq_span_1",
      "semantic_name": "Indexed Library PCR Primer 1",
      "family_label": "indexed_library_pcr_primer_1",
      "sequence_template": "CAAGCAGAAGACGGCATACGAGAT[i7 sample index]GTGACTGGAGTTCAGACGTGTGCTCTTCCGATCT",
      "decision": "accept",
      "note": ""
    }}
  ],
  "uncertain_groups": [],
  "human_review_required": true
}}

Rules:
- Do not add rows.
- Do not output any sequence value except an existing deterministic sequence_template copied from the input row or a template abstraction over copied deterministic sequence text.
- Do not invent, repair, reverse-complement, complete, or normalize raw sequences.
- Use decision=reject only for clear prose false positives or non-oligo rows.
- Use decision=review for uncertain family grouping or ambiguous labels.
- Prefer family labels for plate/table series, for example Round1, Round2, Round3 barcode families and indexed PCR primer families.
- Keep raw sequence ownership with the deterministic row identified by source_span_id.
"""


def run_open_inventory_audit(
    protocol_slug: str,
    inventory: dict[str, Any],
    model: str,
) -> tuple[str, dict[str, Any]]:
    raw = complete_text(
        model=model,
        system=(
            "You are a strict sequencing oligo inventory grouping auditor. Return only JSON. "
            "Never invent, modify, complete, or reverse-complement raw sequence strings."
        ),
        prompt=_build_open_inventory_audit_prompt(protocol_slug, inventory),
    )
    parsed = _extract_json(raw)
    if parsed is None:
        raise ValueError("Open inventory audit model did not return parseable JSON")
    parsed.setdefault("audit_status", "uncertain")
    parsed.setdefault("inventory_annotations", [])
    parsed.setdefault("uncertain_groups", [])
    parsed["human_review_required"] = True
    return raw, parsed


def _open_inventory_rows(
    protocol_slug: str,
    inventory: dict[str, Any],
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    annotations = _inventory_annotations(audit or {})
    template_counts: dict[tuple[str, str], int] = {}
    for seq_candidate in inventory.get("candidates") or []:
        if not isinstance(seq_candidate, dict):
            continue
        sequence = _candidate_string(seq_candidate.get("sequence"))
        if not sequence:
            continue
        annotation = annotations.get(str(seq_candidate.get("source_span_id") or "")) or annotations.get(str(seq_candidate.get("id") or "")) or {}
        if annotation.get("decision") == "reject":
            continue
        family_label = (
            _candidate_string(annotation.get("family_label"))
            or _candidate_string(seq_candidate.get("family_label"))
        )
        sequence_template = (
            _candidate_string(annotation.get("sequence_template"))
            or _candidate_string(seq_candidate.get("sequence_template"))
        )
        if sequence_template and sequence_template != sequence:
            key = _template_count_key(family_label, sequence_template)
            if key:
                template_counts[key] = template_counts.get(key, 0) + 1

    for seq_candidate in inventory.get("candidates") or []:
        if not isinstance(seq_candidate, dict):
            continue
        sequence = _candidate_string(seq_candidate.get("sequence"))
        if not sequence:
            continue
        annotation = annotations.get(str(seq_candidate.get("source_span_id") or "")) or annotations.get(str(seq_candidate.get("id") or "")) or {}
        if annotation.get("decision") == "reject":
            continue
        family_label = (
            _candidate_string(annotation.get("family_label"))
            or _candidate_string(seq_candidate.get("family_label"))
        )
        sequence_template = (
            _candidate_string(annotation.get("sequence_template"))
            or _candidate_string(seq_candidate.get("sequence_template"))
        )
        template_key = _template_count_key(family_label, sequence_template)
        use_template = bool(
            sequence_template
            and sequence_template != sequence
            and (
                template_counts.get(template_key or ("", ""), 0) > 1
                or annotation.get("sequence_template")
            )
        )
        output_sequence = sequence_template if use_template and sequence_template else sequence
        if not output_sequence:
            continue

        identity_label = _source_identity_label(
            seq_candidate,
            annotation,
            family_label,
            use_template=use_template,
        )
        display_name = _display_from_label(identity_label) or identity_label
        record_type = _record_type_from_inventory({**seq_candidate, "family_label": family_label})
        base_id = _slugify(identity_label)
        canonical_id = f"{protocol_slug}_{base_id}" if record_type == "oligo" else base_id
        key = (canonical_id, output_sequence)
        source_span_id = _candidate_string(seq_candidate.get("source_span_id"))
        existing = rows_by_key.get(key)
        if existing:
            if seq_candidate.get("name_hint") and seq_candidate.get("name_hint") not in existing["member_names"]:
                existing["member_names"].append(seq_candidate.get("name_hint"))
            if source_span_id and source_span_id not in existing["source_span_ids"]:
                existing["source_span_ids"].append(source_span_id)
            existing["member_count"] += 1
            if annotation.get("decision") == "review":
                existing["review_status"] = "needs_review"
                existing["uncertainty"] = "audit_requested_review"
            continue
        rows_by_key[key] = {
            "canonical_id": canonical_id,
            "display_name": display_name,
            "name": display_name,
            "member_names": [seq_candidate.get("name_hint")] if seq_candidate.get("name_hint") else [],
            "member_count": 1,
            "family_label": family_label,
            "sequence_template": sequence_template,
            "protocol_slug": protocol_slug,
            "protocol_version": None,
            "record_type": record_type,
            "sequence": output_sequence,
            "orientation": seq_candidate.get("orientation_hint") or "5_to_3",
            "modifications": seq_candidate.get("modifications") or [],
            "source": "deterministic_open",
            "inventory_id": seq_candidate.get("inventory_id"),
            "source_span_ids": [source_span_id] if source_span_id else [],
            "evidence": _oriented_evidence(output_sequence, seq_candidate.get("orientation_hint")),
            "confidence": seq_candidate.get("confidence"),
            "review_status": (
                "needs_review" if annotation.get("decision") == "review" else "accepted_by_audit" if annotation else "accepted_by_rules"
            ),
            "review_note": annotation.get("note"),
            "uncertainty": "audit_requested_review" if annotation.get("decision") == "review" else None,
            "deterministic_candidate_id": seq_candidate.get("id"),
        }
    return sorted(rows_by_key.values(), key=lambda row: (row["record_type"], row["canonical_id"], row["sequence"]))


def _build_audit_prompt(
    protocol_slug: str,
    text: str,
    candidates: list[dict[str, Any]],
    inventory: dict[str, Any],
) -> str:
    candidate_summary = [
        {
            "canonical_id": item.get("canonical_id"),
            "fetch_name": item.get("fetch_name"),
            "fetch_id": item.get("fetch_id"),
            "record_type": item.get("record_type"),
            "protocol_version": item.get("protocol_version"),
        }
        for item in candidates
    ]
    return f"""Audit this name-guided deterministic oligo extraction run.

Protocol slug: {protocol_slug}

Expected candidate names:
{json.dumps(candidate_summary, indent=2, ensure_ascii=False)}

Deterministic sequence inventory:
{json.dumps(inventory, indent=2, ensure_ascii=False)}

Return ONLY valid JSON with this shape:
{{
  "audit_status": "pass",
  "missing_sequences": [],
  "candidate_reviews": [],
  "oligo_terms": [],
  "suspected_regex_gaps": [],
  "proposed_inventory_rows": [],
  "proposed_extractor_changes": [],
  "human_review_required": true
}}

Rules:
- candidate_reviews may only accept/reject/review deterministic candidates by candidate_id or source_span_id.
- Do not generate, normalize, reverse-complement, complete, or repair sequence strings.
- If an expected candidate name appears but the deterministic extractor missed nearby exact bases, report it in missing_sequences with copied source_span and sequence_text copied exactly from source or null.
- sequence_text must be null unless exact bases are visibly present in source_span.

Protocol source text:
{text[:60000]}"""


def run_name_guided_audit(
    protocol_slug: str,
    text: str,
    candidates: list[dict[str, Any]],
    inventory: dict[str, Any],
    model: str,
) -> tuple[str, dict[str, Any]]:
    raw = complete_text(
        model=model,
        system=(
            "You are a strict sequencing oligo extraction auditor. Return only JSON. "
            "Never invent, modify, complete, or reverse-complement sequence strings."
        ),
        prompt=_build_audit_prompt(protocol_slug, text, candidates, inventory),
    )
    parsed = _extract_json(raw)
    if parsed is None:
        raise ValueError("Audit model did not return parseable JSON")
    parsed.setdefault("audit_status", "uncertain")
    parsed.setdefault("missing_sequences", [])
    parsed.setdefault("candidate_reviews", [])
    parsed.setdefault("oligo_terms", [])
    parsed.setdefault("suspected_regex_gaps", [])
    parsed.setdefault("proposed_inventory_rows", [])
    parsed.setdefault("proposed_extractor_changes", [])
    parsed["human_review_required"] = True
    return raw, parsed


def extract_name_guided_deterministic(
    *,
    protocol_slug: str,
    inputs: list[Path],
    candidates: list[dict[str, Any]],
    output: Path,
    artifacts_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
    deterministic_only: bool = False,
    inventory_script: Path | None = None,
) -> dict[str, Any]:
    load_env_local()
    out_dir = artifacts_dir or output.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    _step(f"preparing protocol text for {protocol_slug} from {len(inputs)} source file(s)")
    text = grouped_protocol_text(inputs)
    protocol_text_path = out_dir / f"{protocol_slug}.protocol.txt"
    protocol_text_path.write_text(text, encoding="utf-8")

    script_label = f"generated script {inventory_script}" if inventory_script else "engine master inventory module"
    _step(f"extracting deterministic sequence inventory with {script_label}")
    inventory = extract_sequence_inventory(
        text,
        use_known_inventory=False,
        inventory_script=inventory_script,
    )
    inventory_json = out_dir / f"{protocol_slug}.inventory.json"
    inventory_tsv_path = out_dir / f"{protocol_slug}.sequence-inventory.tsv"
    write_inventory_json(inventory_json, inventory)
    inventory_tsv_path.write_text(inventory_tsv(inventory), encoding="utf-8")
    _step(f"wrote deterministic inventory with {len(inventory.get('candidates') or [])} candidate sequence row(s)")

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
    audit_raw_path = out_dir / f"{protocol_slug}.audit.raw.txt"
    audit_json_path = out_dir / f"{protocol_slug}.audit.json"
    try:
        if not deterministic_only and candidates:
            _step("running LLM audit for name-guided sequence matches")
            _step(f"LLM audit model: {model}")
            raw_audit, audit = run_name_guided_audit(protocol_slug, text, candidates, inventory, model)
        elif not deterministic_only and not candidates:
            _step("running LLM audit for open-extraction family labels and templates")
            _step(f"LLM audit model: {model}")
            raw_audit, audit = run_open_inventory_audit(protocol_slug, inventory, model)
        else:
            _step("skipping LLM audit")
    except Exception as exc:
        raw_audit = str(exc)
        audit = {
            "audit_status": "llm_audit_failed",
            "missing_sequences": [],
            "candidate_reviews": [],
            "inventory_annotations": [],
            "uncertain_groups": [],
            "oligo_terms": [],
            "suspected_regex_gaps": [],
            "proposed_inventory_rows": [],
            "proposed_extractor_changes": [],
            "human_review_required": True,
            "error": str(exc),
        }
        _step(f"LLM audit failed; continuing with deterministic rows: {exc}")
    audit_raw_path.write_text(raw_audit, encoding="utf-8")
    _write_json(audit_json_path, audit)
    _step(f"audit status: {audit.get('audit_status')}")

    _step("building benchmark output rows")
    review_decisions = _review_decisions(audit)
    source_spans = inventory.get("source_spans") or {}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str]] = set()
    if candidates:
        for candidate in candidates:
            matched_any = False
            for seq_candidate in inventory.get("candidates") or []:
                if not isinstance(seq_candidate, dict):
                    continue
                if not _sequence_near_candidate(text, seq_candidate, candidate):
                    continue
                if review_decisions.get(str(seq_candidate.get("id"))) == "reject":
                    continue
                span_id = _candidate_string(seq_candidate.get("source_span_id"))
                if span_id and review_decisions.get(span_id) == "reject":
                    continue
                sequence = _candidate_string(seq_candidate.get("sequence"))
                key = (
                    _candidate_string(candidate.get("canonical_id")),
                    _candidate_string(candidate.get("protocol_version")),
                    sequence or "",
                )
                if key in seen:
                    continue
                seen.add(key)
                evidence = str(seq_candidate.get("source_text") or sequence or "")
                rows.append(
                    _adapter_primer_row(
                        protocol_slug,
                        candidate,
                        seq_candidate,
                        evidence,
                        span_id,
                        source="deterministic",
                    )
                )
                matched_any = True
            if not matched_any and _candidate_name_found(text, candidate):
                rows.append(
                    _adapter_primer_row(
                        protocol_slug,
                        candidate,
                        None,
                        _candidate_string(candidate.get("fetch_name")) or "",
                        None,
                        source="deterministic",
                    )
                )
    else:
        rows = _open_inventory_rows(protocol_slug, inventory, audit)

    if candidates:
        mode = "name_guided_deterministic" if deterministic_only else "name_guided_deterministic_audit"
    else:
        mode = "open_deterministic" if deterministic_only else "open_deterministic_audit"

    result = {
        "schema_version": 1,
        "mode": mode,
        "protocol_slug": protocol_slug,
        "sources": [str(path) for path in inputs],
        "model": None if deterministic_only else model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol": {
            "adapter_primer_sequences": rows,
            "source_spans": source_spans,
            "warnings": [],
        },
        "audit": audit,
        "artifacts": {
            "protocol_text": str(protocol_text_path),
            "inventory_json": str(inventory_json),
            "inventory_tsv": str(inventory_tsv_path),
            "audit_raw": str(audit_raw_path),
            "audit_json": str(audit_json_path),
            "inventory_script": str(inventory_script) if inventory_script else None,
        },
    }
    _write_json(output, result)
    _step(f"wrote benchmark JSON with {len(rows)} row(s): {output}")
    return result


def _limit_protocol_text(text: str, limit: int | None) -> tuple[str, str]:
    if limit is None or limit <= 0 or len(text) <= limit:
        return text, ""
    return (
        text[:limit],
        f"\n\n[Protocol source text truncated from {len(text)} to {limit} characters.]",
    )


def _baseline_oligo_skill_text() -> str:
    if not BASELINE_OLIGO_SKILL_PATH.exists():
        return ""
    return BASELINE_OLIGO_SKILL_PATH.read_text(encoding="utf-8").strip()


def _baseline_prompt(
    protocol_slug: str,
    text: str,
    candidates: list[dict[str, Any]],
    *,
    protocol_text_limit: int | None = DEFAULT_BASELINE_PROTOCOL_TEXT_LIMIT,
) -> str:
    prompt_candidates = [
        {
            "canonical_id": item.get("canonical_id"),
            "display_name": item.get("display_name"),
            "fetch_id": item.get("fetch_id"),
            "fetch_name": item.get("fetch_name"),
            "record_type": item.get("record_type"),
            "protocol_version": item.get("protocol_version"),
            "aliases": item.get("aliases") or [],
        }
        for item in candidates
    ]
    protocol_text, truncation_note = _limit_protocol_text(text, protocol_text_limit)
    skill_text = _baseline_oligo_skill_text()
    skill_section = (
        f"\nBaseline oligo extraction skill:\n{skill_text}\n"
        if skill_text
        else ""
    )
    return f"""Protocol slug: {protocol_slug}
{skill_section}

Candidate adapter/primer/oligo names:
{json.dumps(prompt_candidates, indent=2, ensure_ascii=False)}

Extract oligo records only. Return ONLY valid JSON as a ProtocolOligoSet:
{{
  "protocol_id": "{protocol_slug}",
  "protocol_name": "{protocol_slug}",
  "split": "train",
  "source_files": [],
  "oligos": [
    {{
      "oligo_id": "",
      "protocol_id": "{protocol_slug}",
      "protocol_name": "{protocol_slug}",
      "name": "",
      "aliases": [],
      "role": "adapter",
      "kind": "single",
      "sequence": "",
      "direction": "5_to_3",
      "components": [],
      "sequence_source": "explicit_in_protocol",
      "memory_id": null,
      "evidence": [
        {{"source_id": null, "page": null, "section": null, "quote": ""}}
      ],
      "notes": null
    }}
  ],
  "notes": null
}}

Rules:
- Exhaustiveness is required. List all possible adapter, primer, oligo, sequencing-primer, index-primer, linker, blocking-strand, and collapsed family records supported by exact source text.
- Do not stop after bead, gel-bead, or barcode oligos. Inspect all source sections, including final library/product constructs and sequencing sections.
- Do a final coverage pass over every source line containing "oligo", "primer", "adapter", "adaptor", "bead", "index", "sample index", "read 1", "read 2", "P5", "P7", "Nextera", "TruSeq", "TSO", "cDNA", "pre-amp", "PCR", "linker", or "blocking".
- For 10x-style protocols, include every exact sequence-bearing bead/gel-bead oligo, cDNA primer, pre-amplification primer, sample-index/library PCR primer, P5/P7 adapter segment, read/index sequencing primer, and final library/product construct segment when bases are printed.
- A response with only one or two records is almost always incomplete unless the full protocol source contains only one or two exact sequence-bearing oligo/primer/adapter entries.
- Use candidate names as name-only guidance. Prefer exact candidate canonical_id values when they match, but output real protocol-supported held-out items even when no candidate ID is exact.
- Return only records supported by copied source text.
- evidence must include exact copied sequence/evidence text from the source.
- Do not infer, repair, complete, reverse-complement, or generate raw sequence strings.
- Collapse repeated plate/table rows into generalized family records with bracket placeholders when the source rows share a backbone.
- Do not collapse distinct named oligos, adapters, primers, sequencing primers, index primers, or PCR primers into one record.
- If a candidate name is present but exact bases are absent, include it with sequence=null and sequence_source=not_shown_in_protocol.

Protocol source text:
{protocol_text}{truncation_note}"""


def _baseline_evidence_text(item: dict[str, Any]) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, list):
        quotes = [
            entry.get("quote")
            for entry in evidence
            if isinstance(entry, dict) and _candidate_string(entry.get("quote"))
        ]
        return " | ".join(str(quote) for quote in quotes)
    return _candidate_string(item.get("sequence")) or ""


def baseline_oligo_extract(
    *,
    protocol_slug: str,
    inputs: list[Path],
    candidates: list[dict[str, Any]],
    output: Path,
    artifacts_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
    protocol_text_limit: int | None = DEFAULT_BASELINE_PROTOCOL_TEXT_LIMIT,
) -> dict[str, Any]:
    load_env_local()
    out_dir = artifacts_dir or output.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    text = grouped_protocol_text(inputs)
    protocol_text_path = out_dir / f"{protocol_slug}.baseline-oligo.protocol.txt"
    prompt_path = out_dir / f"{protocol_slug}.baseline-oligo.prompt.txt"
    raw_path = out_dir / f"{protocol_slug}.baseline-oligo.raw.txt"
    protocol_text_path.write_text(text, encoding="utf-8")
    prompt = _baseline_prompt(
        protocol_slug,
        text,
        candidates,
        protocol_text_limit=protocol_text_limit,
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    raw = complete_text(
        model=model,
        system=(
            "You are a strict sequencing protocol oligo extractor. Return only JSON. "
            "Never invent, modify, complete, or reverse-complement sequence strings."
        ),
        prompt=prompt,
    )
    raw_path.write_text(raw, encoding="utf-8")
    parsed = _extract_json(raw)
    if parsed is None:
        raise ValueError("baseline-oligo model did not return parseable JSON")
    candidate_by_id: dict[Any, dict[str, Any]] = {}
    for item in candidates:
        for key in ["canonical_id", "output_id", "fetch_id"]:
            if item.get(key):
                candidate_by_id[item[key]] = item
    rows: list[dict[str, Any]] = []
    raw_rows = parsed.get("records") or parsed.get("adapter_primer_sequences") or parsed.get("oligos") or []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        canonical = item.get("canonical_id") or item.get("fetch_id") or item.get("output_id") or item.get("oligo_id")
        candidate = candidate_by_id.get(canonical, {})
        evidence = _baseline_evidence_text(item)
        rows.append(
            {
                "canonical_id": candidate.get("output_id") or canonical,
                "display_name": item.get("display_name") or candidate.get("display_name") or item.get("name"),
                "name": item.get("name") or candidate.get("fetch_name") or item.get("display_name"),
                "protocol_slug": protocol_slug,
                "protocol_version": item.get("protocol_version", candidate.get("protocol_version")),
                "record_type": item.get("record_type") or item.get("role") or candidate.get("record_type") or "oligo",
                "sequence": item.get("sequence"),
                "orientation": item.get("orientation") or item.get("direction") or "unknown",
                "modifications": item.get("modifications") or [],
                "source": item.get("sequence_source") or "llm_named_missing",
                "inventory_id": None,
                "source_span_ids": item.get("source_span_ids") or [],
                "evidence": evidence,
                "confidence": item.get("confidence"),
                "review_status": "accepted",
                "review_note": None,
                "uncertainty": item.get("uncertainty"),
            }
        )
    result = {
        "schema_version": 1,
        "mode": "baseline_oligo",
        "protocol_slug": protocol_slug,
        "sources": [str(path) for path in inputs],
        "model": model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol": {
            "adapter_primer_sequences": rows,
            "source_spans": parsed.get("source_spans") or {},
            "warnings": parsed.get("warnings") or [],
        },
        "raw": raw,
        "artifacts": {
            "protocol_text": str(protocol_text_path),
            "prompt": str(prompt_path),
            "raw": str(raw_path),
        },
    }
    _write_json(output, result)
    return result

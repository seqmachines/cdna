#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from sequence_inventory import extract_sequence_inventory


def extract_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass

    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", text)
    if fence_match:
        try:
            value = json.loads(fence_match.group(1))
            return value if isinstance(value, dict) else None
        except Exception:
            pass

    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            value = json.loads(brace_match.group(0))
            return value if isinstance(value, dict) else None
        except Exception:
            pass

    return None


def build_context(text: str) -> dict[str, Any]:
    inventory = extract_sequence_inventory(text)
    candidates = inventory["candidates"]
    prompt_block = ""

    if candidates:
        prompt_block = f"""

Deterministic sequence inventory:
{json.dumps({"sequence_candidates": candidates, "source_spans": inventory["source_spans"]}, indent=2, ensure_ascii=False)}

Sequence inventory rules:
- Treat sequence_candidates as the authoritative set of exact copied sequences.
- adapter_primer_sequences may only use non-null sequence strings that appear exactly in sequence_candidates.
- You may label roles, names, orientation, modifications, and uncertainty.
- You may include entries with "sequence": null for named sequences mentioned in the protocol but missing exact bases.
- Do not create, normalize, reverse-complement, or repair sequence strings."""

    audit_prompt = f"""Audit this sequencing protocol for missed adapter, primer, oligo, index, or platform sequence elements.

Return ONLY valid JSON with this shape:
{{
  "audit_status": "pass",
  "missing_sequences": [
    {{
      "name_hint": null,
      "sequence_text": null,
      "source_span": "",
      "reason_script_missed_it": "",
      "suggested_regex_case": null
    }}
  ],
  "candidate_reviews": [
    {{
      "candidate_id": "",
      "source_span_id": "",
      "decision": "accept",
      "confidence": 0.0,
      "suggested_name": null,
      "suggested_role": null,
      "sequence": null,
      "reason": ""
    }}
  ],
  "oligo_terms": [
    {{
      "name_hint": null,
      "sequence_text": null,
      "source_span": "",
      "matched_candidate_ids": [],
      "reason": ""
    }}
  ],
  "suspected_regex_gaps": [],
  "proposed_inventory_rows": [
    {{
      "id": null,
      "name": null,
      "sequence": null,
      "role": null,
      "platform": null,
      "protocol": null,
      "source_url": null,
      "orientation": null,
      "modifications": null,
      "notes": null
    }}
  ],
  "proposed_extractor_changes": [],
  "human_review_required": true
}}

Rules:
- audit_status must be one of "pass", "missing_candidates_found", or "uncertain".
- You are auditing the deterministic extractor; do not rewrite code.
- oligo_terms must list adapter, primer, oligo, index, or platform sequence names/terms found in the protocol text, whether or not an exact sequence is printed nearby.
- For each oligo_terms item, matched_candidate_ids must refer only to deterministic candidate ids from sequence_candidates.
- candidate_reviews must review deterministic extractor candidates. decision must be "accept", "reject", or "review".
- candidate_reviews.confidence is your confidence from 0.0 to 1.0 that the candidate is a real adapter, primer, oligo, index, or platform sequence copied from the protocol.
- Reject candidates that are English words, legal boilerplate, PDF headers/footers, or prose accidentally matching IUPAC nucleotide letters.
- Do not generate, rewrite, normalize, repair, reverse-complement, complete, or otherwise change any candidate sequence strings.
- In candidate_reviews, sequence must always be null. You may only accept/reject/review existing candidate_id/source_span_id rows and suggest clearer names/roles.
- If a sequence-like oligo is missing, quote the exact source text in source_span.
- If an oligo is named but exact bases are absent, set sequence_text to null. Do not infer or generate the missing bases.
- proposed_inventory_rows are suggestions only and must be human reviewed.
- Do not invent sequence strings. sequence_text must be copied from protocol text or null.

Deterministic extractor result:
{json.dumps(inventory, indent=2, ensure_ascii=False)}

Protocol text:
{text[:50000]}"""
    return {"inventory": inventory, "prompt_block": prompt_block, "audit_prompt": audit_prompt}


def parse_audit(raw_text: str) -> dict[str, Any]:
    parsed = extract_json(raw_text)
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
    return parsed


def finalize_protocol(raw_text: str, inventory: dict[str, Any]) -> dict[str, Any]:
    protocol = extract_json(raw_text)
    if protocol is None:
        raise ValueError("Model did not return parseable JSON")

    candidates = inventory.get("candidates") or []
    if candidates:
        allowed_sequences = {candidate.get("sequence") for candidate in candidates}
        for entry in protocol.get("adapter_primer_sequences") or []:
            sequence = entry.get("sequence")
            if sequence and sequence not in allowed_sequences:
                raise ValueError(f"Model emitted sequence not found in deterministic inventory: {sequence}")

        protocol["source_spans"] = {
            **(inventory.get("source_spans") or {}),
            **(protocol.get("source_spans") or {}),
        }
        if not protocol.get("adapter_primer_sequences"):
            warnings = protocol.setdefault("warnings", [])
            warnings.append(
                "Deterministic sequence candidates were extracted, but the model did not include adapter_primer_sequences entries."
            )

    return protocol


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: protocol_parse_support.py <build-context|finalize> ...", file=sys.stderr)
        return 2

    command = sys.argv[1]
    try:
        if command == "build-context":
            if len(sys.argv) != 3:
                print("Usage: protocol_parse_support.py build-context <text_path>", file=sys.stderr)
                return 2
            text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="ignore")
            print(json.dumps(build_context(text), ensure_ascii=False))
            return 0

        if command == "finalize":
            if len(sys.argv) != 4:
                print("Usage: protocol_parse_support.py finalize <raw_text_path> <inventory_json_path>", file=sys.stderr)
                return 2
            raw_text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="ignore")
            inventory = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
            print(json.dumps(finalize_protocol(raw_text, inventory), ensure_ascii=False))
            return 0

        if command == "parse-audit":
            if len(sys.argv) != 3:
                print("Usage: protocol_parse_support.py parse-audit <raw_audit_path>", file=sys.stderr)
                return 2
            raw_text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="ignore")
            print(json.dumps(parse_audit(raw_text), ensure_ascii=False))
            return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

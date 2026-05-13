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
    if not candidates:
        return {"inventory": inventory, "prompt_block": ""}

    prompt_block = f"""

Deterministic sequence inventory:
{json.dumps({"sequence_candidates": candidates, "source_spans": inventory["source_spans"]}, indent=2, ensure_ascii=False)}

Sequence inventory rules:
- Treat sequence_candidates as the authoritative set of exact copied sequences.
- adapter_primer_sequences may only use non-null sequence strings that appear exactly in sequence_candidates.
- You may label roles, names, orientation, modifications, and uncertainty.
- You may include entries with "sequence": null for named sequences mentioned in the protocol but missing exact bases.
- Do not create, normalize, reverse-complement, or repair sequence strings."""
    return {"inventory": inventory, "prompt_block": prompt_block}


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
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

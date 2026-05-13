#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

LABEL_TERMS = [
    "primer",
    "adapter",
    "adaptor",
    "oligo",
    "tso",
    "template switch",
    "p5",
    "p7",
    "read 1",
    "read1",
    "read 2",
    "read2",
    "i5",
    "i7",
    "index",
    "bead",
    "splint",
    "ligation",
    "capture",
]

SEQUENCE_PATTERN = re.compile(
    r"(?:/[^/\s]+/|\([dD]?[ACGTUacgtu]\)\d*|[rR][ACGTUacgtu]|[ACGTURYSWKMBDHVNacgturyswkmbdhvn]){8,}"
)


def infer_role_hint(line: str) -> str | None:
    lower = line.lower()
    if "template switch" in lower or "tso" in lower:
        return "template_switch_oligo"
    if "bead" in lower:
        return "bead_oligo"
    if "read 1" in lower or "read1" in lower:
        return "read_1_primer"
    if "read 2" in lower or "read2" in lower:
        return "read_2_primer"
    if "i5" in lower:
        return "sample_index_i5"
    if "i7" in lower:
        return "sample_index_i7"
    if "index" in lower:
        return "sample_index"
    if "adapter" in lower or "adaptor" in lower:
        return "adapter"
    if "primer" in lower:
        return "primer"
    if "oligo" in lower:
        return "oligo"
    return None


def infer_orientation(line: str) -> str:
    compact = re.sub(r"\s+", "", line)
    if re.search(r"5['’′]?-?>.*3['’′]?", compact):
        return "5_to_3"
    if re.search(r"3['’′]?-?>.*5['’′]?", compact):
        return "3_to_5"
    return "unknown"


def extract_modifications(sequence: str) -> list[str]:
    modifications = set()
    modifications.update(re.findall(r"/[^/\s]+/", sequence))
    modifications.update(re.findall(r"[rR][ACGTUacgtu]", sequence))
    modifications.update(re.findall(r"\([dD]?[ACGTUacgtu]\)\d*", sequence))
    return sorted(modifications)


def has_sequence_label(line: str) -> bool:
    lower = line.lower()
    return any(term in lower for term in LABEL_TERMS)


def base_like_length(sequence: str) -> int:
    return len(re.findall(r"[ACGTURYSWKMBDHVNacgturyswkmbdhvn]", sequence))


def is_likely_sequence(sequence: str, line: str) -> bool:
    base_length = base_like_length(sequence)
    has_modification = bool(re.search(r"/[^/\s]+/|[rR][ACGTUacgtu]|\([dD]?[ACGTUacgtu]\)", sequence))
    label_nearby = has_sequence_label(line)

    if base_length >= 18:
        return True
    if label_nearby and base_length >= 8:
        return True
    if has_modification and base_length >= 6:
        return True
    return False


def infer_name_hint(line: str, sequence: str) -> str | None:
    idx = line.find(sequence)
    before = line[:idx] if idx >= 0 else line
    cleaned = re.sub(r"^[\s|,;:-]+", "", before)
    cleaned = re.sub(r"[\s|,;:-]+$", "", cleaned)
    cleaned = re.sub(r"^sequence$", "", cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        return None
    return cleaned[-120:]


def extract_sequence_inventory(text: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    source_spans: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()

    line_start = 0
    for line in re.split(r"\r?\n", text):
        for match in SEQUENCE_PATTERN.finditer(line):
            sequence = match.group(0)
            start = line_start + match.start()
            end = start + len(sequence)

            if not is_likely_sequence(sequence, line):
                continue

            key = f"{sequence}:{line.strip()}"
            if key in seen:
                continue
            seen.add(key)

            source_span_id = f"seq_span_{len(candidates) + 1}"
            label_nearby = has_sequence_label(line)
            source_spans[source_span_id] = {
                "text": line.strip(),
                "page": None,
                "section": None,
                "start": start,
                "end": end,
            }

            candidates.append(
                {
                    "id": f"seq_{len(candidates) + 1}",
                    "name_hint": infer_name_hint(line, sequence),
                    "role_hint": infer_role_hint(line),
                    "sequence": sequence,
                    "orientation_hint": infer_orientation(line),
                    "modifications": extract_modifications(sequence),
                    "source_span_id": source_span_id,
                    "source_text": line.strip(),
                    "start": start,
                    "end": end,
                    "confidence": 0.85 if label_nearby else 0.55,
                }
            )
        line_start += len(line) + 1

    return {"candidates": candidates[:200], "source_spans": source_spans}


def main() -> int:
    if len(sys.argv) > 2:
        print("Usage: sequence_inventory.py [text_path]", file=sys.stderr)
        return 2

    if len(sys.argv) == 2:
        text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore")
    else:
        text = sys.stdin.read()

    print(json.dumps(extract_sequence_inventory(text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

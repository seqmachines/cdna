#!/usr/bin/env python3
from __future__ import annotations

import json
import csv
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
ORIENTED_SEQUENCE_PATTERN = re.compile(
    r"(?P<left>[53])\s*['’′]\s*-?\s*(?P<body>.*?)\s*-?\s*(?P<right>[53])\s*['’′]"
)
PLACEHOLDER_PATTERN = re.compile(r"\[[^\]\n]{1,100}\]")

REQUIRED_INVENTORY_COLUMNS = [
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
INVENTORY_FILE_NAME = "oligos.tsv"


def inventory_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "sequence_inventory"


def inventory_file(root: Path | None = None) -> Path:
    return (root or inventory_dir()) / INVENTORY_FILE_NAME


def load_inventory_rows(root: Path | None = None) -> list[dict[str, str]]:
    root = root or inventory_dir()
    path = inventory_file(root)
    if not path.exists():
        return []

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = reader.fieldnames or []
        missing = [column for column in REQUIRED_INVENTORY_COLUMNS if column not in columns]
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        for row in reader:
            row = {key: (value or "").strip() for key, value in row.items()}
            row["_inventory_file"] = str(path.relative_to(root.parent.parent))
            if row.get("id") and row.get("sequence"):
                rows.append(row)
    return rows


def whitespace_normalize(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    position_map: list[int] = []
    for idx, char in enumerate(text):
        if char.isspace():
            continue
        normalized_chars.append(char)
        position_map.append(idx)
    return "".join(normalized_chars), position_map


def inventory_sequence_key(sequence: str) -> str:
    return "".join(sequence.split())


def sequence_key(sequence: str) -> str:
    return re.sub(r"\s+", "", sequence).upper()


def context_line(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()


def split_modifications(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[;,]", value) if item.strip()]


def match_known_inventory(text: str, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    normalized_text, position_map = whitespace_normalize(text)
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()

    for row in rows:
        needle = inventory_sequence_key(row["sequence"])
        if not needle:
            continue
        start_idx = 0
        while True:
            idx = normalized_text.find(needle, start_idx)
            if idx == -1:
                break
            original_start = position_map[idx]
            original_end = position_map[idx + len(needle) - 1] + 1
            key = (row["id"], original_start, original_end)
            if key not in seen:
                seen.add(key)
                matches.append(
                    {
                        "inventory_id": row["id"],
                        "inventory_file": row.get("_inventory_file", ""),
                        "name": row["name"],
                        "role": row["role"],
                        "platform": row["platform"],
                        "protocol": row["protocol"],
                        "source_url": row["source_url"],
                        "sequence": row["sequence"],
                        "orientation": row["orientation"] or "unknown",
                        "modifications": split_modifications(row["modifications"]),
                        "notes": row["notes"],
                        "start": original_start,
                        "end": original_end,
                        "source_text": context_line(text, original_start, original_end),
                    }
                )
            start_idx = idx + 1
    return matches


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
    searchable = PLACEHOLDER_PATTERN.sub("", sequence)
    modifications.update(re.findall(r"/[^/\s]+/", searchable))
    modifications.update(re.findall(r"[rR][ACGTUacgtu]", searchable))
    modifications.update(re.findall(r"\([dD]?[ACGTUacgtu]\)\d*", searchable))
    return sorted(modifications)


def normalize_sequence_body(body: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in PLACEHOLDER_PATTERN.finditer(body):
        parts.append(re.sub(r"\s+", "", body[cursor : match.start()]))
        placeholder = re.sub(r"\s+", " ", match.group(0).strip())
        parts.append(placeholder)
        cursor = match.end()
    parts.append(re.sub(r"\s+", "", body[cursor:]))
    sequence = "".join(parts)
    sequence = re.sub(r"^[|\\/\-–—>]+", "", sequence)
    sequence = re.sub(r"[|\\/\-–—>]+$", "", sequence)
    return sequence.strip()


def sequence_has_placeholder(sequence: str) -> bool:
    return bool(PLACEHOLDER_PATTERN.search(sequence))


def has_sequence_label(line: str) -> bool:
    lower = line.lower()
    return any(term in lower for term in LABEL_TERMS)


def base_like_length(sequence: str) -> int:
    without_placeholders = PLACEHOLDER_PATTERN.sub("", sequence)
    return len(re.findall(r"[ACGTURYSWKMBDHVNacgturyswkmbdhvn]", without_placeholders))


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


def clean_label_text(value: str) -> str | None:
    cleaned = re.sub(r"^[\s>*\-•]+", "", value)
    cleaned = re.sub(r"[|\\/\-–—>\s]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" :;")
    if not cleaned:
        return None
    return cleaned[-160:]


def label_before_orientation(line: str, match: re.Match[str]) -> str | None:
    before = line[: match.start()]
    return clean_label_text(before)


def is_nested_line(line: str) -> bool:
    return len(line) > len(line.lstrip())


def combine_name(parent_label: str | None, local_label: str | None, orientation: str, nested: bool) -> str | None:
    if local_label and parent_label and nested:
        return f"{parent_label} - {local_label}"
    if local_label:
        return local_label
    if parent_label:
        return f"{parent_label} - {orientation} strand"
    return None


def infer_parent_label(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.endswith(":"):
        return None
    if not has_sequence_label(stripped):
        return None
    return clean_label_text(stripped[:-1])


def is_contained_in_larger_candidate(
    candidate: dict[str, Any], candidates: list[dict[str, Any]]
) -> bool:
    candidate_key = sequence_key(candidate["sequence"])
    for other in candidates:
        if candidate is other:
            continue
        if candidate.get("source_text") != other.get("source_text"):
            continue
        if candidate["start"] < other["start"] or candidate["end"] > other["end"]:
            continue
        other_key = sequence_key(other["sequence"])
        if len(other_key) > len(candidate_key):
            return True
    return False


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        candidate
        for candidate in candidates
        if not is_contained_in_larger_candidate(candidate, candidates)
    ]

    preferred_source = {"known_inventory": 3, "deterministic": 2, "regex": 1}
    best_by_context: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = (sequence_key(candidate["sequence"]), candidate["source_text"])
        current = best_by_context.get(key)
        if current is None:
            best_by_context[key] = candidate
            continue
        current_score = preferred_source.get(current.get("source", ""), 0)
        candidate_score = preferred_source.get(candidate.get("source", ""), 0)
        if candidate_score > current_score:
            best_by_context[key] = candidate

    return sorted(best_by_context.values(), key=lambda item: (item["start"], item["end"], item["sequence"]))


def extract_sequence_inventory(text: str) -> dict[str, Any]:
    raw_candidates: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_spans: dict[str, dict[str, Any]] = {}
    occupied_ranges: list[tuple[int, int]] = []

    parent_label: str | None = None
    line_start = 0
    for line in re.split(r"\r?\n", text):
        oriented_matches = list(ORIENTED_SEQUENCE_PATTERN.finditer(line))
        if oriented_matches:
            for match in oriented_matches:
                sequence = normalize_sequence_body(match.group("body"))
                if not sequence:
                    continue
                base_length = base_like_length(sequence)
                if base_length < 8 and not sequence_has_placeholder(sequence):
                    continue

                orientation = "5_to_3" if match.group("left") == "5" and match.group("right") == "3" else "3_to_5"
                local_label = label_before_orientation(line, match)
                nested = is_nested_line(line)
                name_hint = combine_name(parent_label, local_label, orientation, is_nested_line(line))
                role_context = " ".join(
                    item
                    for item in [
                        parent_label if nested or not local_label else "",
                        local_label or "",
                        line,
                    ]
                    if item
                )
                source_text = line.strip()
                start = line_start + match.start("body")
                end = line_start + match.end("body")
                occupied_ranges.append((start, end))
                raw_candidates.append(
                    {
                        "source": "deterministic",
                        "name_hint": name_hint,
                        "role_hint": infer_role_hint(role_context),
                        "sequence": sequence,
                        "orientation_hint": orientation,
                        "modifications": extract_modifications(sequence),
                        "source_text": source_text,
                        "start": start,
                        "end": end,
                        "confidence": 0.92 if has_sequence_label(source_text) or parent_label else 0.75,
                    }
                )
        else:
            next_parent_label = infer_parent_label(line)
            if next_parent_label:
                parent_label = next_parent_label

        line_start += len(line) + 1

    inventory_rows = load_inventory_rows()
    for match in match_known_inventory(text, inventory_rows):
        raw_candidates.append(
            {
                "source": "known_inventory",
                "inventory_id": match["inventory_id"],
                "inventory_file": match["inventory_file"],
                "name_hint": match["name"],
                "role_hint": match["role"],
                "sequence": match["sequence"],
                "orientation_hint": match["orientation"],
                "modifications": match["modifications"],
                "source_text": match["source_text"],
                "start": match["start"],
                "end": match["end"],
                "confidence": 0.98,
                "platform": match["platform"],
                "protocol": match["protocol"],
                "source_url": match["source_url"],
                "notes": match["notes"],
            }
        )
        occupied_ranges.append((match["start"], match["end"]))

    seen: set[str] = set()
    line_start = 0
    for line in re.split(r"\r?\n", text):
        for match in SEQUENCE_PATTERN.finditer(line):
            sequence = match.group(0)
            start = line_start + match.start()
            end = start + len(sequence)

            if any(start >= range_start and end <= range_end for range_start, range_end in occupied_ranges):
                continue

            if not is_likely_sequence(sequence, line):
                continue

            key = f"{sequence}:{line.strip()}"
            if key in seen:
                continue
            seen.add(key)

            label_nearby = has_sequence_label(line)
            raw_candidates.append(
                {
                    "source": "regex",
                    "name_hint": infer_name_hint(line, sequence),
                    "role_hint": infer_role_hint(line),
                    "sequence": sequence,
                    "orientation_hint": infer_orientation(line),
                    "modifications": extract_modifications(sequence),
                    "source_text": line.strip(),
                    "start": start,
                    "end": end,
                    "confidence": 0.85 if label_nearby else 0.55,
                }
            )
        line_start += len(line) + 1

    for candidate in dedupe_candidates(raw_candidates)[:200]:
        span_prefix = "inv_span" if candidate["source"] == "known_inventory" else "seq_span"
        source_span_id = f"{span_prefix}_{len(candidates) + 1}"
        span: dict[str, Any] = {
            "text": candidate["source_text"],
            "page": None,
            "section": None,
            "start": candidate["start"],
            "end": candidate["end"],
        }
        if candidate.get("inventory_id"):
            span["inventory_id"] = candidate["inventory_id"]
        if candidate.get("inventory_file"):
            span["inventory_file"] = candidate["inventory_file"]
        source_spans[source_span_id] = span

        entry = dict(candidate)
        entry["id"] = (
            f"known_{candidate['inventory_id']}_{len(candidates) + 1}"
            if candidate["source"] == "known_inventory"
            else f"seq_{len(candidates) + 1}"
        )
        entry["source_span_id"] = source_span_id
        candidates.append(entry)

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

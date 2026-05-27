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
    "primers",
    "adapter",
    "adapters",
    "adaptor",
    "adaptors",
    "oligo",
    "oligos",
    "oligonucleotide",
    "oligonucleotides",
    "oligonucleotie",
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
    "indexed",
    "barcode",
    "barcoded",
    "barcodes",
    "well barcode",
    "rt barcode",
    "rt barcodes",
    "round 1",
    "round 2",
    "round 3",
    "bead",
    "beads",
    "splint",
    "ligation",
    "linker",
    "capture",
    "barcode linker",
    "blocking",
    "blocking strand",
    "blocker",
    "tn5",
    "tagment",
    "tagmentation",
    "truseq",
    "nextera",
    "illumina",
    "cdna",
    "rt",
    "pcr",
    "library",
    "strand",
    "upper",
    "lower",
    "top",
    "bottom",
    "damid",
    "atac",
    "rna",
    "sgrna",
    "feature",
    "sample",
    "dual index",
    "index plate",
    "plate",
    "fam",
    "anti",
    "seq a",
    "seqa",
    "seq b",
    "seqb",
    "sequence",
    "primer type",
    "wellposition",
    "well position",
    "sublibrary",
    "dtv",
    "oligo dt",
    "dt(15)vn",
    "poly(dt)",
    "random hexamer",
]

TOKEN_PATTERN = r"(?:/[^/\s]+/|\([dD]?[ACGTUacgtu]\)\d*|[rR][ACGTUacgtu]|\+[ACGTUacgtu]|[ACGTURYSWKMBDHVNacgturyswkmbdhvn])"
SEQUENCE_PATTERN = re.compile(rf"{TOKEN_PATTERN}{{8,}}")
SEQUENCE_AT_END_PATTERN = re.compile(rf"(?P<sequence>{TOKEN_PATTERN}{{8,}})\s*$")
ORIENTED_SEQUENCE_PATTERN = re.compile(
    r"(?P<left>[53])\s*['’′]\s*-?\s*(?P<body>.*?)\s*-?\s*(?P<right>[53])\s*['’′]"
)
PLACEHOLDER_PATTERN = re.compile(r"\[[^\]\n]{1,100}\]")
ID_LIKE_PATTERN = re.compile(r"^[A-Za-z]{1,16}[_-]?[A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*$")

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
MAX_CANDIDATES = 2000


def inventory_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "sequence_inventory"


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
    if "round1" in lower or "round 1" in lower:
        if "random hexamer" in lower:
            return "round_1_random_hexamer_rt_primer"
        if "dt(15)" in lower or "dtv" in lower or "oligo-dt" in lower or "oligo dt" in lower:
            return "round_1_anchored_poly_dt_rt_primer"
        return "round_1_barcode_oligo"
    if "round2" in lower or "round 2" in lower:
        if "blocking" in lower:
            return "round_2_blocking_strand"
        if "linker" in lower:
            return "round_2_barcode_linker"
        return "round_2_ligation_oligo"
    if "round3" in lower or "round 3" in lower:
        if "blocking" in lower:
            return "round_3_blocking_strand"
        if "linker" in lower:
            return "round_3_barcode_linker"
        return "round_3_ligation_oligo"
    if "nextera" in lower and "primer" in lower:
        return "nextera_tagmentation_pcr_primer"
    if "template switch" in lower or "tso" in lower:
        return "template_switch_oligo"
    if "bead" in lower:
        return "bead_oligo"
    if "tn5" in lower or "tagment" in lower:
        return "tagmentation_oligo"
    if "ligation" in lower or "splint" in lower:
        return "ligation_oligo"
    if "cdna" in lower and "primer" in lower:
        return "cdna_primer"
    if "pcr" in lower and "primer" in lower:
        return "pcr_primer"
    if "rt" in lower and ("primer" in lower or "oligo" in lower):
        return "reverse_transcription_oligo"
    if "read 1" in lower or "read1" in lower:
        return "read_1_primer"
    if "read 2" in lower or "read2" in lower:
        return "read_2_primer"
    if "p5" in lower:
        return "p5_adapter"
    if "p7" in lower:
        return "p7_adapter"
    if "i5" in lower:
        return "sample_index_i5"
    if "i7" in lower:
        return "sample_index_i7"
    if "sublibrary" in lower or "index" in lower or "barcode" in lower:
        return "sample_index"
    if "adapter" in lower or "adaptor" in lower:
        return "adapter"
    if "primer" in lower:
        return "primer"
    if "oligo" in lower or "oligonucleotide" in lower or "oligonucleotie" in lower:
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
    modifications.update(re.findall(r"\+[ACGTUacgtu]", searchable))
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
    sequence = re.sub(r"^[|\\\-–—>]+", "", sequence)
    sequence = re.sub(r"[|\\\-–—>]+$", "", sequence)
    return sequence.strip()


def sequence_has_placeholder(sequence: str) -> bool:
    return bool(PLACEHOLDER_PATTERN.search(sequence))


def has_sequence_label(line: str) -> bool:
    lower = line.lower()
    return any(term in lower for term in LABEL_TERMS)


def strip_nonbase_modifications(sequence: str) -> str:
    value = PLACEHOLDER_PATTERN.sub("", sequence)
    value = re.sub(r"/[^/\s]+/", "", value)
    value = re.sub(r"[rR]([ACGTUacgtu])", r"\1", value)
    value = re.sub(r"\+([ACGTUacgtu])", r"\1", value)
    value = re.sub(r"\([dD]?([ACGTUacgtu])\)\d*", r"\1", value)
    return value


def has_unexpected_lowercase(sequence: str) -> bool:
    value = strip_nonbase_modifications(sequence)
    return bool(re.search(r"[a-z]", value))


def base_like_length(sequence: str) -> int:
    value = strip_nonbase_modifications(sequence)
    return len(re.findall(r"[ACGTURYSWKMBDHVNacgturyswkmbdhvn]", value))


def is_likely_sequence(sequence: str, line: str) -> bool:
    if has_unexpected_lowercase(sequence):
        return False

    base_length = base_like_length(sequence)
    has_modification = bool(re.search(r"/[^/\s]+/|[rR][ACGTUacgtu]|\+[ACGTUacgtu]|\([dD]?[ACGTUacgtu]\)", sequence))
    label_nearby = has_sequence_label(line)

    if sequence_has_placeholder(sequence) and label_nearby:
        return True
    if base_length >= 18:
        return True
    if label_nearby and base_length >= 8:
        return True
    if has_modification and base_length >= 6:
        return True
    return False


def is_likely_table_sequence(value: str) -> bool:
    oriented = ORIENTED_SEQUENCE_PATTERN.search(value)
    sequence = normalize_sequence_body(oriented.group("body") if oriented else value)
    if not sequence or base_like_length(sequence) < 8:
        return False
    if has_unexpected_lowercase(sequence):
        return False
    if sequence_has_placeholder(sequence):
        return True
    if SEQUENCE_PATTERN.fullmatch(sequence):
        return True
    return bool(SEQUENCE_PATTERN.search(sequence)) and base_like_length(sequence) >= 12


def normalize_table_sequence_cell(value: str) -> tuple[str, str, int, int]:
    oriented = ORIENTED_SEQUENCE_PATTERN.search(value)
    if oriented:
        sequence = normalize_sequence_body(oriented.group("body"))
        orientation = "5_to_3" if oriented.group("left") == "5" and oriented.group("right") == "3" else "3_to_5"
        return sequence, orientation, oriented.start("body"), oriented.end("body")
    sequence = normalize_sequence_body(value)
    return sequence, "unknown", len(value) - len(value.lstrip()), len(value.rstrip())


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


def normalize_header_cell(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", value).strip().lower()
    return re.sub(r"\s+", " ", cleaned)


def is_sequence_header_cell(value: str) -> bool:
    normalized = normalize_header_cell(value)
    if normalized == "sequence":
        return True
    if normalized.endswith(" sequence") and not normalized.startswith("source"):
        return True
    return False


def split_tsv_with_spans(line: str, line_start: int) -> list[tuple[str, int, int]]:
    cells: list[tuple[str, int, int]] = []
    offset = 0
    for cell in line.split("\t"):
        start = line_start + offset
        end = start + len(cell)
        cells.append((cell, start, end))
        offset += len(cell) + 1
    return cells


def detect_header_from_cells(cells: list[str]) -> dict[str, Any] | None:
    normalized = [normalize_header_cell(cell) for cell in cells]
    sequence_indices = [idx for idx, cell in enumerate(cells) if is_sequence_header_cell(cell)]
    if not sequence_indices:
        return None

    informative_headers = {
        "description",
        "name",
        "wellposition",
        "well position",
        "primer type",
        "oligonucleotide number",
        "oligonucleotie number",
        "oligo number",
        "number",
        "id",
        "barcode",
    }
    has_informative_header = any(
        header in informative_headers or "description" in header or "primer type" in header
        for idx, header in enumerate(normalized)
        if idx not in sequence_indices
    )
    if not has_informative_header:
        return None

    return {
        "columns": cells,
        "normalized_columns": normalized,
        "sequence_index": sequence_indices[-1],
        "delimiter": "tab",
    }


def detect_space_header(line: str) -> dict[str, Any] | None:
    normalized = normalize_header_cell(line)
    if "sequence" not in normalized:
        return None
    if "oligonucleotie number" in normalized or "oligonucleotide number" in normalized:
        return {
            "columns": ["Oligonucleotide Number", "Description", "Sequence"],
            "normalized_columns": ["oligonucleotide number", "description", "sequence"],
            "sequence_index": 2,
            "delimiter": "space_general",
        }
    if "wellposition" in normalized and "primer type" in normalized and "name" in normalized:
        return {
            "columns": ["WellPosition", "Primer Type", "Name", "Sequence"],
            "normalized_columns": ["wellposition", "primer type", "name", "sequence"],
            "sequence_index": 3,
            "delimiter": "space_well",
        }
    return None


def first_table_value(table: dict[str, Any], cells: list[str], exact: set[str], contains: tuple[str, ...] = ()) -> str:
    normalized = table.get("normalized_columns", [])
    for idx, header in enumerate(normalized):
        if idx >= len(cells):
            continue
        if header in exact or any(item in header for item in contains):
            value = cells[idx].strip()
            if value:
                return value
    return ""


def infer_table_name(table: dict[str, Any], cells: list[str], sheet: str | None) -> str | None:
    sequence_index = table.get("sequence_index", -1)
    identifier = first_table_value(
        table,
        cells,
        {"id", "number", "oligo number", "oligonucleotide number", "oligonucleotie number"},
        ("oligonucleotide number", "oligonucleotie number"),
    )
    name = first_table_value(table, cells, {"name"})
    description = first_table_value(table, cells, {"description"}, ("description",))
    well = first_table_value(table, cells, {"well", "wellposition", "well position"})
    primer_type = first_table_value(table, cells, {"primer type", "type"})

    if not identifier and cells:
        first = cells[0].strip()
        if sequence_index != 0 and ID_LIKE_PATTERN.match(first):
            identifier = first

    parts: list[str] = []
    for value in (identifier, name, description):
        if value and value not in parts:
            parts.append(value)

    if not parts:
        for idx, value in enumerate(cells):
            value = value.strip()
            if value and idx != sequence_index:
                parts.append(value)
            if len(parts) >= 3:
                break

    extras: list[str] = []
    if primer_type and primer_type not in parts:
        extras.append(primer_type)
    if well and well not in parts:
        extras.append(f"well {well}")

    if sheet and len(parts) < 2 and not any("round" in part.lower() for part in parts):
        parts.insert(0, sheet)

    return clean_label_text(" - ".join(parts + extras))


def extract_table_candidate(
    table: dict[str, Any],
    cells_with_spans: list[tuple[str, int, int]],
    line: str,
    source_file: str | None,
    page: int | None,
    sheet: str | None,
) -> dict[str, Any] | None:
    sequence_index = table.get("sequence_index", -1)
    if sequence_index < 0 or sequence_index >= len(cells_with_spans):
        return None

    raw_cell, cell_start, _cell_end = cells_with_spans[sequence_index]
    raw_sequence = raw_cell.strip()
    if not is_likely_table_sequence(raw_sequence):
        return None

    sequence, orientation, sequence_start, sequence_end = normalize_table_sequence_cell(raw_cell)
    start = cell_start + sequence_start
    end = cell_start + sequence_end
    cells = [cell for cell, _start, _end in cells_with_spans]
    name_hint = infer_table_name(table, cells, sheet)
    non_sequence_cells = [cell.strip() for idx, cell in enumerate(cells) if idx != sequence_index and cell.strip()]
    role_context = " ".join(item for item in [sheet or "", name_hint or "", " ".join(non_sequence_cells)] if item)

    return {
        "source": "table",
        "name_hint": name_hint,
        "role_hint": infer_role_hint(role_context),
        "sequence": sequence,
        "orientation_hint": orientation if orientation != "unknown" else infer_orientation(line),
        "modifications": extract_modifications(sequence),
        "source_text": line.strip(),
        "start": start,
        "end": end,
        "confidence": 0.99,
        "source_file": source_file,
        "page": page,
        "section": sheet,
    }


def extract_space_table_candidate(
    table: dict[str, Any],
    line: str,
    line_start: int,
    source_file: str | None,
    page: int | None,
    sheet: str | None,
) -> dict[str, Any] | None:
    stripped = line.strip()
    match = SEQUENCE_AT_END_PATTERN.search(stripped)
    if not match:
        return None

    raw_sequence = match.group("sequence").strip()
    if not is_likely_table_sequence(raw_sequence):
        return None

    prefix = stripped[: match.start("sequence")].strip()
    if not prefix:
        return None

    delimiter = table.get("delimiter")
    if delimiter == "space_general":
        pieces = prefix.split(None, 1)
        if len(pieces) < 2:
            return None
        cells = [pieces[0], pieces[1], raw_sequence]
    elif delimiter == "space_well":
        before_name = prefix.rsplit(None, 1)
        if len(before_name) < 2:
            return None
        well_and_type, name = before_name
        well_type_parts = well_and_type.split(None, 1)
        if len(well_type_parts) < 2:
            return None
        cells = [well_type_parts[0], well_type_parts[1], name, raw_sequence]
    else:
        return None

    line_sequence_start = line.find(raw_sequence)
    if line_sequence_start < 0:
        return None
    sequence, orientation, sequence_start, sequence_end = normalize_table_sequence_cell(raw_sequence)
    start = line_start + line_sequence_start + sequence_start
    end = line_start + line_sequence_start + sequence_end
    name_hint = infer_table_name(table, cells, sheet)
    role_context = " ".join(item for item in [sheet or "", name_hint or "", prefix] if item)

    return {
        "source": "table",
        "name_hint": name_hint,
        "role_hint": infer_role_hint(role_context),
        "sequence": sequence,
        "orientation_hint": orientation if orientation != "unknown" else infer_orientation(line),
        "modifications": extract_modifications(sequence),
        "source_text": line.strip(),
        "start": start,
        "end": end,
        "confidence": 0.96,
        "source_file": source_file,
        "page": page,
        "section": sheet,
    }


def iter_lines_with_offsets(text: str):
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        yield line, offset
        offset += len(raw_line)


def infer_family_label(candidate: dict[str, Any]) -> str | None:
    role = candidate.get("role_hint")
    context = " ".join(
        str(candidate.get(key) or "")
        for key in ["name_hint", "role_hint", "source_text", "section"]
    ).lower()
    if "nextera" in context and "primer" in context:
        return "nextera_tagmentation_pcr_primer"
    if "template switch" in context or "tso" in context:
        return "template_switching_oligo"
    if "round1" in context or "round 1" in context:
        if "random hexamer" in context:
            return "round1_random_hexamer_rt_primer"
        if "dt(15)" in context or "dtv" in context or "oligo-dt" in context or "oligo dt" in context:
            return "round1_oligo_dt_vn_rt_primer"
        return "round1_barcode_oligo"
    if "round2" in context or "round 2" in context:
        if "blocking" in context:
            return "round2_blocking_strand"
        if "linker" in context:
            return "round2_barcode_linker"
        return "round2_ligation_barcode"
    if "round3" in context or "round 3" in context:
        if "blocking" in context:
            return "round3_blocking_strand"
        if "linker" in context:
            return "round3_barcode_linker"
        return "round3_ligation_barcode"
    if role in {
        "bead_oligo",
        "cdna_primer",
        "pcr_primer",
        "read_1_primer",
        "read_2_primer",
        "p5_adapter",
        "p7_adapter",
        "sample_index",
        "sample_index_i5",
        "sample_index_i7",
    }:
        return role
    if role in {"adapter", "primer", "oligo"}:
        return None
    return role if isinstance(role, str) and role else None


def generalized_sequence_template(sequence: str, family_label: str | None) -> str:
    template = sequence
    modification = ""
    body = sequence
    modification_match = re.match(r"(/[^/\s]+/)(.*)$", sequence)
    if modification_match:
        modification = modification_match.group(1)
        body = modification_match.group(2)

    if family_label == "round1_oligo_dt_vn_rt_primer":
        poly_t = re.search(r"T{10,}VN$", body, flags=re.IGNORECASE)
        if poly_t:
            poly_start = max(poly_t.start(), poly_t.end() - 2 - 15)
            barcode_start = poly_start - 8
        else:
            barcode_start = -1
            poly_start = -1
        if barcode_start >= 0:
            template = (
                modification
                + body[:barcode_start]
                + "[8-bp Round1 barcode]"
                + body[poly_start:]
            )
    elif family_label == "round1_random_hexamer_rt_primer":
        random_hexamer = re.search(r"N{6}$", body, flags=re.IGNORECASE)
        if random_hexamer and random_hexamer.start() >= 8:
            template = (
                modification
                + body[: random_hexamer.start() - 8]
                + "[8-bp Round1 barcode]"
                + body[random_hexamer.start() :]
            )
    elif family_label == "round2_ligation_barcode":
        if len(body) >= 31:
            template = modification + body[:-23] + "[8-bp Round2 barcode]" + body[-15:]
    elif family_label == "round3_ligation_barcode":
        umi_match = re.search(r"N{10}", body, flags=re.IGNORECASE)
        if umi_match and len(body) >= umi_match.end() + 8:
            template = (
                modification
                + body[: umi_match.start()]
                + "[10-bp UMI][8-bp Round3 barcode]"
                + body[umi_match.end() + 8 :]
            )
    elif family_label == "nextera_tagmentation_pcr_primer":
        if len(body) >= 56:
            index_length = 6
            template = body[:24] + "[6-bp i7 sample index]" + body[24 + index_length :]
    template = re.sub(
        r"N{6,}",
        lambda match: f"[{len(match.group(0))}-bp variable]",
        template,
        flags=re.IGNORECASE,
    )
    if family_label == "round1_random_hexamer_rt_primer":
        template = template.replace("[6-bp variable]", "NNNNNN")
    return template


def annotate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    entry = dict(candidate)
    family_label = infer_family_label(entry)
    if family_label:
        entry["family_label"] = family_label
        entry["sequence_template"] = generalized_sequence_template(entry["sequence"], family_label)
    return entry


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

    preferred_source = {"table": 4, "known_inventory": 3, "deterministic": 2, "regex": 1}
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


def extract_sequence_inventory(
    text: str,
    *,
    use_known_inventory: bool = True,
) -> dict[str, Any]:
    raw_candidates: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_spans: dict[str, dict[str, Any]] = {}
    occupied_ranges: list[tuple[int, int]] = []

    current_source_file: str | None = None
    current_page: int | None = None
    current_sheet: str | None = None
    table: dict[str, Any] | None = None
    parent_label: str | None = None

    for line, line_start in iter_lines_with_offsets(text):
        stripped = line.strip()

        source_match = re.match(r"\[\[SOURCE_FILE:\s*(.*?)\s*\]\]", stripped)
        if source_match:
            current_source_file = source_match.group(1)
            current_page = None
            current_sheet = None
            table = None
            parent_label = None
            continue

        page_match = re.match(r"\[\[PAGE\s+(\d+)\]\]", stripped)
        if page_match:
            current_page = int(page_match.group(1))
            table = None
            continue

        sheet_match = re.match(r"\[\[SHEET:\s*(.*?)\s*\]\]", stripped)
        if sheet_match:
            current_sheet = sheet_match.group(1)
            current_page = None
            table = None
            parent_label = None
            continue

        if "\t" in line:
            cells_with_spans = split_tsv_with_spans(line, line_start)
            cells = [cell for cell, _start, _end in cells_with_spans]
            detected_header = detect_header_from_cells(cells)
            if detected_header:
                table = detected_header
                continue

            if table:
                candidate = extract_table_candidate(
                    table,
                    cells_with_spans,
                    line,
                    current_source_file,
                    current_page,
                    current_sheet,
                )
                if candidate:
                    raw_candidates.append(candidate)
                    occupied_ranges.append((candidate["start"], candidate["end"]))
                continue

        detected_space_header = detect_space_header(line)
        if detected_space_header:
            table = detected_space_header
            continue

        if table:
            candidate = extract_space_table_candidate(
                table,
                line,
                line_start,
                current_source_file,
                current_page,
                current_sheet,
            )
            if candidate:
                raw_candidates.append(candidate)
                occupied_ranges.append((candidate["start"], candidate["end"]))
                continue

        oriented_matches = list(ORIENTED_SEQUENCE_PATTERN.finditer(line))
        if oriented_matches:
            for match in oriented_matches:
                sequence = normalize_sequence_body(match.group("body"))
                if not sequence:
                    continue
                if base_like_length(sequence) < 8 and not sequence_has_placeholder(sequence):
                    continue
                if not is_likely_sequence(sequence, line):
                    continue

                orientation = "5_to_3" if match.group("left") == "5" and match.group("right") == "3" else "3_to_5"
                local_label = label_before_orientation(line, match)
                nested = is_nested_line(line)
                name_hint = combine_name(parent_label, local_label, orientation, nested)
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
                        "source_file": current_source_file,
                        "page": current_page,
                        "section": current_sheet,
                    }
                )
        else:
            next_parent_label = infer_parent_label(line)
            if next_parent_label:
                parent_label = next_parent_label

    if use_known_inventory:
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
    regex_source_file: str | None = None
    regex_page: int | None = None
    regex_sheet: str | None = None
    for line, line_start in iter_lines_with_offsets(text):
        stripped = line.strip()
        source_match = re.match(r"\[\[SOURCE_FILE:\s*(.*?)\s*\]\]", stripped)
        if source_match:
            regex_source_file = source_match.group(1)
            regex_page = None
            regex_sheet = None
            continue
        page_match = re.match(r"\[\[PAGE\s+(\d+)\]\]", stripped)
        if page_match:
            regex_page = int(page_match.group(1))
            continue
        sheet_match = re.match(r"\[\[SHEET:\s*(.*?)\s*\]\]", stripped)
        if sheet_match:
            regex_sheet = sheet_match.group(1)
            regex_page = None
            continue
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
                    "sequence": normalize_sequence_body(sequence),
                    "orientation_hint": infer_orientation(line),
                    "modifications": extract_modifications(sequence),
                    "source_text": line.strip(),
                    "start": start,
                    "end": end,
                    "confidence": 0.85 if label_nearby else 0.55,
                    "source_file": regex_source_file,
                    "page": regex_page,
                    "section": regex_sheet,
                }
            )

    for candidate in dedupe_candidates(raw_candidates)[:MAX_CANDIDATES]:
        span_prefix = "inv_span" if candidate["source"] == "known_inventory" else "seq_span"
        source_span_id = f"{span_prefix}_{len(candidates) + 1}"
        span: dict[str, Any] = {
            "text": candidate["source_text"],
            "page": candidate.get("page"),
            "section": candidate.get("section"),
            "start": candidate["start"],
            "end": candidate["end"],
        }
        if candidate.get("source_file"):
            span["source_file"] = candidate["source_file"]
        if candidate.get("inventory_id"):
            span["inventory_id"] = candidate["inventory_id"]
        if candidate.get("inventory_file"):
            span["inventory_file"] = candidate["inventory_file"]
        source_spans[source_span_id] = span

        entry = annotate_candidate(candidate)
        entry["id"] = (
            f"known_{candidate['inventory_id']}_{len(candidates) + 1}"
            if candidate["source"] == "known_inventory"
            else f"seq_{len(candidates) + 1}"
        )
        entry["source_span_id"] = source_span_id
        candidates.append(entry)

    return {"candidates": candidates, "source_spans": source_spans}


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

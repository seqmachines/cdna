from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cdna_engine.io import prepare_protocol_text

from .normalizer import infer_direction, normalize_sequence
from .schema import Evidence


SUPPORTED_INPUT_SUFFIXES = {".pdf", ".xlsx", ".txt", ".tsv", ".csv", ".html", ".htm", ".md"}
NAME_TERMS = [
    "primer",
    "adapter",
    "adaptor",
    "oligo",
    "bead",
    "tso",
    "read 1",
    "read1",
    "read 2",
    "read2",
    "p5",
    "p7",
    "nextera",
    "truseq",
    "index",
    "barcode",
    "umi",
    "hairpin",
    "tn5",
]
NAME_RE = re.compile(r"\b(" + "|".join(re.escape(term) for term in NAME_TERMS) + r")\b", re.I)
ORIENTED_RE = re.compile(
    r"(?P<raw>[35]\s*['’′ʹ]\s*-?\s*(?P<body>[\s\S]{1,700}?)\s*-?\s*[35]\s*['’′ʹ])",
    re.I,
)
TOKEN_RE = (
    r"(?:"
    r"/[^/\s]+/"
    r"|\[[^\]\n]{1,100}\]"
    r"|[ACGTUacgtu]\(\d{1,3}\)"
    r"|\([dD]?[ACGTUacgtu]\)"
    r"|[rR][ACGTUacgtu]"
    r"|\+[ACGTUacgtu]"
    r"|[*]"
    r"|[ACGTURYSWKMBDHVNacgturyswkmbdhvn]"
    r")"
)
SEQUENCE_RE = re.compile(rf"{TOKEN_RE}{{8,}}")


def source_files(input_path: Path) -> list[Path]:
    input_path = input_path.expanduser().resolve()
    if input_path.is_file():
        return [input_path] if _is_supported_source_file(input_path) else []
    files = [
        path
        for path in sorted(input_path.rglob("*"))
        if _is_supported_source_file(path)
    ]
    return files


def _is_supported_source_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(".") or path.name.startswith("~$"):
        return False
    return path.name != "groundtruth_oligos.json" and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES


def _block_type(text: str) -> str:
    stripped = text.strip()
    if "\t" in stripped:
        return "table"
    if _is_sequence_context_line(stripped):
        return "sequence_diagram"
    if len(stripped) < 90 and stripped.endswith(":"):
        return "heading"
    return "body"


def _is_sequence_context_line(line: str) -> bool:
    if ORIENTED_RE.search(line):
        return True
    if NAME_RE.search(line) and SEQUENCE_RE.search(line):
        return True
    if re.search(r"\[[^\]]*(barcode|umi|index|bp)[^\]]*\]", line, flags=re.I):
        return True
    return False


def _is_sequence_continuation_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if ORIENTED_RE.search(stripped) or SEQUENCE_RE.search(stripped):
        return True
    token_prefix = re.match(
        rf"^(?:{TOKEN_RE}){{6,}}(?:\s*[-–—]?\s*[35]\s*['’′ʹ])?",
        stripped,
        flags=re.I,
    )
    return bool(token_prefix and re.search(r"[35]\s*['’′ʹ]", stripped))


def parse_text_blocks(text: str, source_id: str, start_index: int = 1) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    page: int | None = None
    section: str | None = None
    sequence_group: list[str] = []

    def flush_sequence_group() -> None:
        nonlocal sequence_group
        if not sequence_group:
            return
        block_id = f"block_{start_index + len(blocks):05d}"
        quote = "\n".join(sequence_group).strip()
        blocks.append(
            {
                "block_id": block_id,
                "source_id": source_id,
                "page": page,
                "section": section,
                "block_type": "sequence_diagram" if len(sequence_group) > 1 else _block_type(quote),
                "text": quote,
            }
        )
        sequence_group = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        page_match = re.match(r"\[\[PAGE\s+(\d+)\]\]", stripped)
        if page_match:
            flush_sequence_group()
            page = int(page_match.group(1))
            section = None
            continue
        sheet_match = re.match(r"\[\[SHEET:\s*(.*?)\s*\]\]", stripped)
        if sheet_match:
            flush_sequence_group()
            section = sheet_match.group(1).strip() or None
            page = None
            continue
        if not stripped:
            flush_sequence_group()
            continue
        if sequence_group and _is_sequence_continuation_line(line):
            sequence_group.append(line)
            continue
        if _is_sequence_context_line(line):
            sequence_group.append(line)
            continue
        flush_sequence_group()
        block_id = f"block_{start_index + len(blocks):05d}"
        if len(stripped) < 120 and (stripped.endswith(":") or re.match(r"^\d+(?:\.\d+)*\s+\S", stripped)):
            section = stripped.rstrip(":")
        blocks.append(
            {
                "block_id": block_id,
                "source_id": source_id,
                "page": page,
                "section": section,
                "block_type": _block_type(stripped),
                "text": stripped,
            }
        )
    flush_sequence_group()
    return blocks


def parse_input_blocks(input_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    files = source_files(input_path)
    blocks: list[dict[str, Any]] = []
    source_ids: list[str] = []
    for file_path in files:
        source_id = file_path.name
        source_ids.append(str(file_path))
        text = prepare_protocol_text(file_path)
        blocks.extend(parse_text_blocks(text, source_id, start_index=len(blocks) + 1))
    return blocks, source_ids


def _base_like_length(sequence: str) -> int:
    without_mods = re.sub(r"/[^/]+/", "", sequence)
    without_placeholders = re.sub(r"\[[^\]]+\]", "", without_mods)
    return len(re.findall(r"[ACGTURYSWKMBDHVNacgturyswkmbdhvn]", without_placeholders))


def _has_placeholder(sequence: str) -> bool:
    return bool(re.search(r"\[[^\]]+\]", sequence))


def _looks_like_sequence(raw: str, nearby_text: str, block_type: str | None = None, direction: str = "unknown") -> bool:
    normalized = normalize_sequence(raw)
    base_like_length = _base_like_length(normalized)
    if base_like_length < 8 and not _has_placeholder(normalized):
        return False
    if direction in {"5_to_3", "3_to_5"} and block_type == "sequence_diagram" and base_like_length >= 8:
        return True
    letters = re.sub(r"r[ACGTU]", "", normalized, flags=re.I)
    letters = re.sub(r"/[^/]+/|\[[^\]]+\]|\([^)]+\)|[*+]", "", letters)
    if re.search(r"[a-z]", letters):
        return False
    if not NAME_RE.search(nearby_text) and base_like_length < 14 and not _has_placeholder(normalized):
        return False
    return True


def _evidence_for_block(block: dict[str, Any]) -> Evidence:
    return Evidence(
        source_id=str(block.get("source_id") or ""),
        page=block.get("page"),
        section=block.get("section"),
        quote=str(block.get("text") or "").strip() or None,
    )


def infer_name_from_text(text: str, fallback: str) -> str:
    line = " ".join(part.strip() for part in text.splitlines() if part.strip())
    if ":" in line:
        prefix = line.split(":", 1)[0].strip(" -|")
        if prefix and NAME_RE.search(prefix) and not SEQUENCE_RE.search(prefix):
            return re.sub(r"\s+", " ", prefix)[:120]
    match = NAME_RE.search(line)
    if match:
        start = max(0, match.start() - 50)
        end = min(len(line), match.end() + 50)
        phrase = line[start:end].strip(" -|:;,.")
        return re.sub(r"\s+", " ", phrase)[:120]
    return fallback


def infer_name_for_sequence(text: str, sequence_start: int, fallback: str) -> str:
    line_start = text.rfind("\n", 0, sequence_start) + 1
    prefix = text[line_start:sequence_start]
    prefix = re.sub(r"\s+", " ", prefix).strip(" \t(-:;,")
    prefix = re.sub(r"(?:[35]\s*['’′ʹ]\s*-?\s*)?\d+\s*$", "", prefix).strip(" \t(-:;,")
    prefix = re.sub(r"[35]\s*['’′ʹ]\s*$", "", prefix).strip(" \t(-:;,")
    patterns = [
        r"(?i)\b((?:indexed\s+)?P[57]\s+primer)\s*$",
        r"(?i)\b((?:anchored\s+)?oligo-dT(?:\(VN\))?\s+primer)\s*$",
        r"(?i)\b(oligo-dT(?:\(VN\))?)\s*$",
        r"(?i)\b((?:Illumina\s+|Nextera\s+|TruSeq\s+)?[A-Za-z0-9/+_.-][A-Za-z0-9/+_.\- ]{0,70}?(?:primer|adapter|adaptor|oligo))\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prefix)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()[:120]
    return infer_name_from_text(text, fallback)


def scan_sequence_candidates(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def local_block(block: dict[str, Any], text: str, start: int, end: int) -> dict[str, Any]:
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end == -1:
            line_end = len(text)
        copied = dict(block)
        copied["text"] = text[line_start:line_end].strip() or text.strip()
        return copied

    def add_candidate(block: dict[str, Any], raw: str, direction: str, name_hint: str | None = None) -> bool:
        normalized = normalize_sequence(raw)
        if not _looks_like_sequence(raw, str(block.get("text") or ""), str(block.get("block_type") or ""), direction):
            return False
        key = (normalized, str(block.get("block_id")), raw)
        if key in seen:
            return False
        seen.add(key)
        candidate_id = f"seq_{len(candidates) + 1:04d}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "block_id": block.get("block_id"),
                "raw_sequence": raw.strip(),
                "normalized_sequence": normalized,
                "direction": direction if direction in {"5_to_3", "3_to_5"} else "unknown",
                "nearby_text": str(block.get("text") or "").strip(),
                "name_hint": name_hint or infer_name_from_text(str(block.get("text") or ""), f"Sequence candidate {len(candidates) + 1}"),
                "evidence": _evidence_for_block(block).model_dump(mode="json"),
            }
        )
        return True

    for block in blocks:
        text = str(block.get("text") or "")
        occupied: list[tuple[int, int]] = []
        for match in ORIENTED_RE.finditer(text):
            raw = match.group("raw")
            direction = infer_direction(raw)
            name_hint = infer_name_for_sequence(text, match.start(), f"Sequence candidate {len(candidates) + 1}")
            if add_candidate(local_block(block, text, match.start(), match.end()), raw, direction, name_hint):
                occupied.append((match.start(), match.end()))
        for match in SEQUENCE_RE.finditer(text):
            if any(match.start() >= start and match.end() <= end for start, end in occupied):
                continue
            name_hint = infer_name_for_sequence(text, match.start(), f"Sequence candidate {len(candidates) + 1}")
            add_candidate(local_block(block, text, match.start(), match.end()), match.group(0), "unknown", name_hint)
    return candidates


def scan_name_mentions(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        text = str(block.get("text") or "")
        for line_index, raw_line in enumerate(text.splitlines() or [text], start=1):
            line = raw_line.strip()
            if not NAME_RE.search(line):
                continue
            line_block = dict(block)
            line_block["text"] = line
            name = infer_name_from_text(line, f"Oligo mention {len(mentions) + 1}")
            key = (name.lower(), str(block.get("block_id")), str(line_index))
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                {
                    "mention_id": f"name_{len(mentions) + 1:04d}",
                    "block_id": block.get("block_id"),
                    "name": name,
                    "nearby_text": line,
                    "evidence": _evidence_for_block(line_block).model_dump(mode="json"),
                }
            )
    return mentions

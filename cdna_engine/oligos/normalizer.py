from __future__ import annotations

import re


OUTER_ORIENTATION_RE = re.compile(
    r"^\s*(?P<left>[35])\s*['’′ʹ]\s*-?\s*(?P<body>.*?)\s*-?\s*(?P<right>[35])\s*['’′ʹ]\s*$",
    flags=re.S,
)
BASE_REPEAT_RE = re.compile(r"([ACGTUacgtu])\((\d{1,3})\)")


def normalize_sequence(raw: str) -> str:
    """Normalize display formatting without changing biological sequence content."""
    value = raw.strip().replace("–", "-").replace("—", "-")
    match = OUTER_ORIENTATION_RE.match(value)
    if match:
        value = match.group("body")
        left = match.group("left")
        value = re.sub(r"^\s*\d+\s+(?=(?:/|\[|[ACGTURYSWKMBDHVN]))", "", value, flags=re.I)
        if left == "5":
            value = re.sub(r"^/phos/", "/5Phos/", value.strip(), flags=re.I)
    value = _remove_alignment_whitespace(value)
    value = _expand_base_repeats(value)
    value = re.sub(r"(?<=^)/phos/", "/5Phos/", value, flags=re.I)
    value = re.sub(r"(?<=^)/5phos/", "/5Phos/", value, flags=re.I)
    return value


def _expand_base_repeats(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        repeat = int(match.group(2))
        if repeat <= 0 or repeat > 500:
            return match.group(0)
        return match.group(1) * repeat

    return BASE_REPEAT_RE.sub(repl, value)


def _remove_alignment_whitespace(value: str) -> str:
    chars: list[str] = []
    in_placeholder = False
    for char in value:
        if char == "[":
            in_placeholder = True
            chars.append(char)
            continue
        if char == "]":
            in_placeholder = False
            chars.append(char)
            continue
        if char.isspace() and not in_placeholder:
            continue
        chars.append(char)
    return "".join(chars)


def infer_direction(raw: str) -> str:
    match = OUTER_ORIENTATION_RE.match(raw.strip())
    if not match:
        return "unknown"
    if match.group("left") == "5" and match.group("right") == "3":
        return "5_to_3"
    if match.group("left") == "3" and match.group("right") == "5":
        return "3_to_5"
    return "unknown"


def sequence_key(sequence: str | None) -> str:
    if not sequence:
        return ""
    return re.sub(r"\s+", "", normalize_sequence(sequence)).upper()


def display_name_key(name: str) -> str:
    value = name.lower()
    value = re.sub(r"\(?\bpn[-\s]*\d+\)?", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(
        r"\b(oligo|primer|adapter|adaptor|the|a|an|pn|seq|sequence)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()

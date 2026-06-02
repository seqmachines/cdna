from __future__ import annotations

from collections.abc import Iterable, Mapping


def tsv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = ";".join(str(item) for item in value)
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def write_tsv(columns: list[str], rows: Iterable[Mapping[str, object]]) -> str:
    output = ["\t".join(columns)]
    for row in rows:
        output.append("\t".join(tsv_cell(row.get(column)) for column in columns))
    return "\n".join(output) + "\n"


def parse_tsv(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = lines[0].split("\t")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = line.split("\t")
        rows.append({header: cells[index] if index < len(cells) else "" for index, header in enumerate(headers)})
    return rows

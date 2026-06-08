from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from cdna_engine.oligos.normalizer import display_name_key, sequence_key
from cdna_engine.tsv import write_tsv


COLUMNS = [
    "component_memory_id",
    "parent_oligo_id",
    "parent_oligo_name",
    "parent_role",
    "parent_kind",
    "parent_direction",
    "parent_sequence",
    "component_order",
    "component_name",
    "component_role",
    "component_sequence",
    "protocol_count",
    "source_protocol_ids",
    "source_protocol_names",
]
VARIABLE_COMPONENT_ROLES = {"cell_barcode", "umi", "sample_index", "barcode", "rt_barcode", "polyT", "variable"}


def _slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_") or "oligo"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _component_is_fixed_sequence(row: dict[str, Any]) -> bool:
    role = str(row.get("component_role") or "").strip().lower()
    sequence = str(row.get("component_sequence") or "").strip()
    if not sequence or role in VARIABLE_COMPONENT_ROLES:
        return False
    if sequence.lower() == "polyt" or "[" in sequence or "]" in sequence:
        return False
    return bool(re.search(r"[ACGTU]{8,}", sequence, flags=re.I))


def _component_is_assembled_child(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    child_name_key = display_name_key(str(row.get("component_name") or ""))
    child_sequence_key = sequence_key(str(row.get("component_sequence") or ""))
    if not child_name_key or not child_sequence_key:
        return False
    child_parts: list[tuple[str, str]] = []
    for candidate in rows:
        if display_name_key(str(candidate.get("parent_oligo_name") or "")) != child_name_key:
            continue
        if not _component_is_fixed_sequence(candidate):
            continue
        part_sequence_key = sequence_key(str(candidate.get("component_sequence") or ""))
        if not part_sequence_key or part_sequence_key == child_sequence_key:
            continue
        if part_sequence_key in child_sequence_key:
            child_parts.append((part_sequence_key, str(candidate.get("component_role") or "").strip().lower()))
    kept_parts: list[tuple[str, str]] = []
    for part_sequence_key, role in sorted(set(child_parts), key=lambda item: len(item[0]), reverse=True):
        if not any(part_sequence_key in kept_sequence for kept_sequence, _kept_role in kept_parts):
            kept_parts.append((part_sequence_key, role))
    kept_roles = {role for _sequence, role in kept_parts if role}
    return len(kept_parts) >= 2 and len(kept_roles) >= 2


def build_rows(protocol_root: Path) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for gt_path in sorted(protocol_root.glob("*/groundtruth_oligos.json")):
        payload = _read_json(gt_path)
        protocol_id = str(payload.get("protocol_id") or gt_path.parent.name)
        protocol_name = str(payload.get("protocol_name") or protocol_id)
        for oligo in payload.get("oligos") or []:
            if not isinstance(oligo, dict):
                continue
            components = oligo.get("components") or []
            if not isinstance(components, list):
                continue
            for index, component in enumerate(components, start=1):
                if not isinstance(component, dict):
                    continue
                component_name = str(component.get("name") or "").strip()
                component_sequence = str(component.get("sequence") or "").strip()
                if not component_name or not component_sequence:
                    continue
                parent_name = str(oligo.get("name") or "").strip()
                parent_sequence = str(oligo.get("sequence") or "").strip()
                if not parent_name:
                    continue
                key = (
                    display_name_key(parent_name),
                    display_name_key(component_name),
                    sequence_key(component_sequence),
                )
                if key not in rows_by_key:
                    rows_by_key[key] = {
                        "component_memory_id": "",
                        "parent_oligo_id": f"oligo_{_slug(parent_name)}",
                        "parent_oligo_name": parent_name,
                        "parent_role": oligo.get("role") or "",
                        "parent_kind": oligo.get("kind") or "",
                        "parent_direction": oligo.get("direction") or "unknown",
                        "parent_sequence": parent_sequence,
                        "component_order": component.get("order") or index,
                        "component_name": component_name,
                        "component_role": component.get("role") or "",
                        "component_sequence": component_sequence,
                        "protocol_count": 0,
                        "source_protocol_ids": "",
                        "source_protocol_names": "",
                        "_source_protocols": {},
                    }
                source_protocols = rows_by_key[key]["_source_protocols"]
                if isinstance(source_protocols, dict):
                    source_protocols[protocol_id] = protocol_name

    unique_rows = list(rows_by_key.values())
    rows = sorted(
        [row for row in unique_rows if not _component_is_assembled_child(row, unique_rows)],
        key=lambda row: (
            display_name_key(str(row.get("parent_oligo_name") or "")),
            int(row.get("component_order") or 0),
            display_name_key(str(row.get("component_name") or "")),
            sequence_key(str(row.get("component_sequence") or "")),
        ),
    )
    for index, row in enumerate(rows, start=1):
        source_protocols = row.pop("_source_protocols", {})
        if not isinstance(source_protocols, dict):
            source_protocols = {}
        source_ids = sorted(str(protocol_id) for protocol_id in source_protocols)
        row["component_memory_id"] = f"cmem_{index:06d}"
        row["protocol_count"] = len(source_ids)
        row["source_protocol_ids"] = ";".join(source_ids)
        row["source_protocol_names"] = ";".join(str(source_protocols[protocol_id]) for protocol_id in source_ids)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build assembled oligo component memory from curated ground-truth JSON files.")
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = build_rows(args.protocol_root.expanduser().resolve())
    args.out.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.out.expanduser().resolve().write_text(write_tsv(COLUMNS, rows), encoding="utf-8")
    print(f"Wrote {len(rows)} component relationships to {args.out.expanduser().resolve()}")


if __name__ == "__main__":
    main()

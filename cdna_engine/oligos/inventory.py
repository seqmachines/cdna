from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from cdna_engine.paths import SCRIPTS_DIR
from cdna_engine.tsv import write_tsv


def _load_sequence_inventory_module():
    module_path = SCRIPTS_DIR / "sequence_inventory.py"
    spec = importlib.util.spec_from_file_location("sequence_inventory", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sequence_inventory"] = module
    spec.loader.exec_module(module)
    return module


def _load_protocol_support_module():
    _load_sequence_inventory_module()
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    module_path = SCRIPTS_DIR / "protocol_parse_support.py"
    spec = importlib.util.spec_from_file_location("cdna_protocol_parse_support", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_sequence_inventory(text: str) -> dict[str, Any]:
    return _load_sequence_inventory_module().extract_sequence_inventory(text)


def build_protocol_context(text: str) -> dict[str, Any]:
    return _load_protocol_support_module().build_context(text)


def parse_audit(raw_text: str) -> dict[str, Any]:
    return _load_protocol_support_module().parse_audit(raw_text)


def write_inventory_json(path: Path, inventory: dict[str, Any]) -> None:
    path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def inventory_tsv(inventory: dict[str, Any]) -> str:
    columns = [
        "id",
        "source",
        "inventory_id",
        "inventory_file",
        "name_hint",
        "role_hint",
        "sequence",
        "orientation_hint",
        "modifications",
        "source_span_id",
        "heuristic_score",
        "start",
        "end",
        "platform",
        "protocol",
        "source_url",
        "notes",
        "source_text",
    ]
    rows = []
    for candidate in inventory.get("candidates") or []:
        row = dict(candidate)
        row["heuristic_score"] = candidate.get("confidence")
        rows.append(row)
    return write_tsv(columns, rows)

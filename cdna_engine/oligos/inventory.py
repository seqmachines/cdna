from __future__ import annotations

import importlib.util
import json
import sys
import hashlib
from pathlib import Path
from typing import Any

from cdna_engine.paths import SCRIPTS_DIR
from cdna_engine.tsv import write_tsv

from . import sequence_inventory as default_sequence_inventory


def _load_sequence_inventory_module(script_path: Path | None = None):
    module_path = script_path or SCRIPTS_DIR / "sequence_inventory.py"
    module_path = module_path.resolve()
    module_name = "sequence_inventory"
    if script_path is not None:
        digest = hashlib.sha1(str(module_path).encode("utf-8")).hexdigest()[:12]
        module_name = f"sequence_inventory_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
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


def extract_sequence_inventory(
    text: str,
    *,
    use_known_inventory: bool = True,
    inventory_script: Path | None = None,
) -> dict[str, Any]:
    module = default_sequence_inventory if inventory_script is None else _load_sequence_inventory_module(inventory_script)
    return module.extract_sequence_inventory(
        text,
        use_known_inventory=use_known_inventory,
    )


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
        "family_label",
        "sequence_template",
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

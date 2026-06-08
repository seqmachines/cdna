from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from cdna_engine.env import load_env_local
from cdna_engine.llm import complete_with_codex
from cdna_engine.tsv import parse_tsv, write_tsv
from .normalizer import display_name_key, infer_direction, normalize_sequence, sequence_key
from .scanner import parse_input_blocks, scan_name_mentions, scan_sequence_candidates, source_files
from .schema import (
    Evidence,
    Oligo,
    OligoComponent,
    OligoNode,
    ProtocolNode,
    ProtocolOligoEdge,
    ProtocolOligoSet,
)


ROLE_TERMS = {
    "tn5_binding_site": ["tn5 binding site", "mosaic end"],
    "primer_site": ["primer binding site", "primer site", "priming site"],
    "promoter": ["promoter"],
    "probe": ["probe"],
    "primer": ["primer", "read 1", "read1", "read 2", "read2"],
    "adapter": ["adapter", "adaptor", "p5", "p7", "truseq", "nextera"],
    "oligo": ["oligo", "bead", "barcode", "blocking", "tso", "template switch", "linker", "ligation", "splint"],
}
EXTRACTABLE_UNSEQUENCED_NAME_RE = re.compile(
    r"\b(primer|adapter|adaptor|oligo|tso|template switch|p5|p7|nextera|truseq|tn5|read\s*[12]|hairpin)\b",
    re.I,
)
def slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_") or "protocol"


def run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prediction_output_payload(prediction: ProtocolOligoSet) -> dict[str, Any]:
    payload = prediction.model_dump(mode="json")
    for oligo in payload.get("oligos") or []:
        if isinstance(oligo, dict):
            oligo.pop("protocol_id", None)
            oligo.pop("protocol_name", None)
    return payload


def default_split_file(input_root: Path) -> Path | None:
    resolved = input_root.expanduser().resolve()
    candidates = [
        resolved / "protocol_split.tsv",
        resolved.parent / "protocol_split.tsv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_split_map(split_file: Path | None) -> dict[str, str]:
    if split_file is None:
        return {}
    rows = parse_tsv(split_file.expanduser().resolve().read_text(encoding="utf-8"))
    split_by_name: dict[str, str] = {}
    for row in rows:
        row_split = str(row.get("Split") or row.get("split") or "").strip().lower()
        protocol_name = str(row.get("protocol_name") or row.get("Protocol") or row.get("protocol") or row.get("name") or "").strip()
        if row_split not in {"train", "eval", "test"} or not protocol_name:
            continue
        split_by_name[display_name_key(protocol_name)] = row_split
    return split_by_name


def protocol_entries(input_root: Path, split: str = "train", split_file: Path | None = None) -> list[dict[str, Any]]:
    input_root = input_root.expanduser().resolve()
    split_map = load_split_map(split_file)
    gt_paths: list[Path]
    if input_root.is_file() and input_root.name == "groundtruth_oligos.json":
        gt_paths = [input_root]
    elif (input_root / "groundtruth_oligos.json").exists():
        gt_paths = [input_root / "groundtruth_oligos.json"]
    elif (input_root / "protocols").exists():
        gt_paths = sorted((input_root / "protocols").glob("*/groundtruth_oligos.json"))
    else:
        gt_paths = sorted(input_root.glob("*/groundtruth_oligos.json"))

    entries: list[dict[str, Any]] = []
    for gt_path in gt_paths:
        try:
            payload = read_json(gt_path)
        except Exception:
            continue
        protocol_id = str(payload.get("protocol_id") or gt_path.parent.name)
        protocol_name = str(payload.get("protocol_name") or protocol_id)
        assigned_split = split_map.get(display_name_key(protocol_name))
        if split_map and assigned_split != split:
            continue
        entries.append(
            {
                "protocol_id": protocol_id,
                "protocol_name": protocol_name,
                "split": assigned_split or split,
                "protocol_dir": str(gt_path.parent),
                "ground_truth_json": str(gt_path),
                "source_files": [str(path) for path in source_files(gt_path.parent)],
            }
        )
    return entries


def load_protocol_index(protocol_root: Path, split: str = "train") -> dict[str, dict[str, Any]]:
    return {str(item["protocol_id"]): item for item in protocol_entries(protocol_root, split=split)}


def _normalize_component_orders(payload: dict[str, Any]) -> dict[str, Any]:
    for oligo in payload.get("oligos") or []:
        if not isinstance(oligo, dict):
            continue
        components = oligo.get("components")
        if not isinstance(components, list):
            continue
        for index, component in enumerate(components, start=1):
            if isinstance(component, dict) and not component.get("order"):
                component["order"] = index
    return payload


def load_ground_truth(ground_truth_json: Path, protocol_id: str, split: str = "train") -> ProtocolOligoSet:
    payload = _normalize_component_orders(read_json(ground_truth_json))
    protocol_name = str(payload.get("protocol_name") or protocol_id)
    payload.setdefault("protocol_id", protocol_id)
    payload.setdefault("protocol_name", protocol_name)
    payload.setdefault("split", split)
    payload.setdefault("source_files", [])
    return ProtocolOligoSet.model_validate(payload)


def build_runtime_memory(protocol_root: Path) -> dict[str, Any]:
    protocol_index = load_protocol_index(protocol_root)
    protocol_nodes: list[dict[str, Any]] = []
    oligo_nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for metadata in protocol_index.values():
        gt_path = Path(str(metadata.get("ground_truth_json") or ""))
        if not gt_path.exists():
            continue
        ground_truth = load_ground_truth(gt_path, str(metadata["protocol_id"]), "train")
        protocol_nodes.append(
            ProtocolNode(
                protocol_id=ground_truth.protocol_id,
                protocol_name=ground_truth.protocol_name,
                split="train",
                source_files=metadata.get("source_files") or [],
            ).model_dump(mode="json")
        )
        for oligo in ground_truth.oligos:
            node = OligoNode.model_validate(oligo.model_dump(mode="json"))
            oligo_nodes.append(node.model_dump(mode="json"))
            edges.append(
                ProtocolOligoEdge(
                    protocol_id=ground_truth.protocol_id,
                    oligo_id=oligo.oligo_id,
                    appeared_as=oligo.name,
                    sequence_source=oligo.sequence_source,
                    evidence=oligo.evidence,
                    notes=oligo.notes,
                ).model_dump(mode="json")
            )

    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "explicit_ground_truth_folder",
        "protocol_nodes": protocol_nodes,
        "oligo_nodes": oligo_nodes,
        "edges": edges,
    }


def empty_memory() -> dict[str, Any]:
    return {"source": "disabled", "protocol_nodes": [], "oligo_nodes": [], "edges": [], "assembled_component_edges": []}


def _resolve_memory_path(memory_path: Path) -> Path:
    resolved = memory_path.expanduser().resolve()
    if not resolved.is_dir():
        return resolved
    preferred = resolved / "training_oligo_memory.tsv"
    if preferred.exists():
        return preferred
    candidates = sorted(path for path in resolved.glob("*.tsv") if path.is_file() and path.name != "assembled_oligo_component_memory.tsv")
    if not candidates:
        raise ValueError(f"--memory-path directory contains no TSV files: {resolved}")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"--memory-path directory must contain exactly one primary oligo TSV file; found: {names}")
    return candidates[0]


def _component_memory_path(memory_path: Path | None, primary_path: Path | None = None) -> Path | None:
    if memory_path is None:
        return None
    resolved = memory_path.expanduser().resolve()
    if resolved.is_dir():
        candidate = resolved / "assembled_oligo_component_memory.tsv"
        return candidate if candidate.exists() else None
    candidate = resolved.with_name("assembled_oligo_component_memory.tsv")
    if primary_path is not None:
        candidate = primary_path.with_name("assembled_oligo_component_memory.tsv")
    return candidate if candidate.exists() else None


def _split_cell(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _read_tsv_columns(path: Path) -> list[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.split("\t")
    return []


def _load_tsv_runtime_memory(path: Path) -> dict[str, Any]:
    rows = parse_tsv(path.read_text(encoding="utf-8"))
    columns = _read_tsv_columns(path)
    oligo_nodes: list[dict[str, Any]] = []
    for row in rows:
        name = (row.get("oligo_name") or row.get("name") or "").strip()
        sequence = (row.get("oligo_sequence") or row.get("sequence") or "").strip()
        if not name or not sequence:
            continue
        source_protocol_ids = _split_cell(row.get("source_protocol_ids") or row.get("protocol_ids") or "")
        source_protocol_names = _split_cell(row.get("source_protocol_names") or row.get("protocol_names") or "")
        memory_id = row.get("memory_id") or f"memory_{len(oligo_nodes) + 1:04d}"
        oligo_nodes.append(
            {
                **row,
                "memory_id": memory_id,
                "oligo_id": memory_id,
                "name": name,
                "sequence": sequence,
                "direction": row.get("direction") or "unknown",
                "allowed_for_memory_completion": True,
                "source_protocol_ids": source_protocol_ids,
                "source_protocol_names": source_protocol_names,
                "aliases": _split_cell(row.get("aliases") or ""),
                "protocol_count": int(row.get("protocol_count") or len(source_protocol_ids) or 0),
            }
        )
    return {
        "source": str(path),
        "source_format": "tsv",
        "memory_columns": columns,
        "protocol_nodes": [],
        "oligo_nodes": oligo_nodes,
        "edges": [],
        "assembled_component_edges": [],
    }


def _load_component_memory(path: Path) -> list[dict[str, Any]]:
    rows = parse_tsv(path.read_text(encoding="utf-8"))
    edges: list[dict[str, Any]] = []
    for row in rows:
        parent_name = (row.get("parent_oligo_name") or "").strip()
        component_name = (row.get("component_name") or "").strip()
        component_sequence = (row.get("component_sequence") or "").strip()
        if not parent_name or not component_name or not component_sequence:
            continue
        source_protocol_ids = _split_cell(row.get("source_protocol_ids") or "")
        source_protocol_names = _split_cell(row.get("source_protocol_names") or "")
        edges.append(
            {
                **row,
                "component_memory_id": row.get("component_memory_id") or f"component_memory_{len(edges) + 1:04d}",
                "parent_oligo_name": parent_name,
                "parent_oligo_id": row.get("parent_oligo_id") or "",
                "parent_sequence": row.get("parent_sequence") or "",
                "parent_kind": row.get("parent_kind") or "",
                "parent_role": row.get("parent_role") or "",
                "parent_direction": row.get("parent_direction") or "unknown",
                "component_order": int(row.get("component_order") or 0),
                "component_name": component_name,
                "component_role": row.get("component_role") or infer_role(component_name),
                "component_sequence": component_sequence,
                "source_protocol_ids": source_protocol_ids,
                "source_protocol_names": source_protocol_names,
                "protocol_count": int(row.get("protocol_count") or len(source_protocol_ids) or 0),
            }
        )
    return edges


def load_runtime_memory(memory_path: Path | None, use_memory: bool) -> dict[str, Any]:
    if not use_memory:
        return empty_memory()
    if memory_path is None:
        raise ValueError("--memory-path is required when --use-memory is set")
    resolved = _resolve_memory_path(memory_path)
    if resolved.suffix.lower() == ".tsv":
        memory = _load_tsv_runtime_memory(resolved)
    else:
        memory = read_json(resolved)
    component_path = _component_memory_path(memory_path, resolved)
    if component_path is not None:
        memory["component_memory_source"] = str(component_path)
        memory["assembled_component_edges"] = _load_component_memory(component_path)
    else:
        memory.setdefault("assembled_component_edges", [])
    memory.setdefault("source", str(resolved))
    memory.setdefault("protocol_nodes", [])
    memory.setdefault("oligo_nodes", [])
    memory.setdefault("edges", [])
    memory.setdefault("assembled_component_edges", [])
    return memory


def _memory_protocol_ids(item: dict[str, Any]) -> set[str]:
    protocol_ids = item.get("source_protocol_ids")
    if isinstance(protocol_ids, list):
        return {str(value) for value in protocol_ids if str(value)}
    protocol_id = item.get("protocol_id")
    return {str(protocol_id)} if protocol_id else set()


def filter_memory_for_protocol(memory: dict[str, Any], protocol_id: str, split: str) -> dict[str, Any]:
    if not memory.get("oligo_nodes") and not memory.get("assembled_component_edges"):
        return memory
    filtered_nodes: list[dict[str, Any]] = []
    excluded = 0
    excluded_current_only = 0
    excluded_mixed_source = 0
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict):
            continue
        source_protocol_ids = _memory_protocol_ids(item)
        if protocol_id in source_protocol_ids:
            excluded += 1
            if source_protocol_ids == {protocol_id}:
                excluded_current_only += 1
                continue
            else:
                excluded_mixed_source += 1
                item = dict(item)
                original_protocol_ids = list(item.get("source_protocol_ids") or [])
                original_protocol_names = list(item.get("source_protocol_names") or [])
                remaining_protocol_ids = [value for value in original_protocol_ids if str(value) != protocol_id]
                item["source_protocol_ids"] = remaining_protocol_ids
                if isinstance(item.get("source_protocol_names"), list) and len(original_protocol_names) == len(original_protocol_ids):
                    item["source_protocol_names"] = [
                        name
                        for pid, name in zip(original_protocol_ids, original_protocol_names, strict=False)
                        if str(pid) != protocol_id
                    ]
                item["protocol_count"] = max(1, int(item.get("protocol_count") or len(remaining_protocol_ids) or 1) - 1)
        filtered_nodes.append(item)
    filtered_component_edges: list[dict[str, Any]] = []
    component_excluded = 0
    component_excluded_current_only = 0
    component_excluded_mixed_source = 0
    for item in memory.get("assembled_component_edges") or []:
        if not isinstance(item, dict):
            continue
        source_protocol_ids = _memory_protocol_ids(item)
        if protocol_id in source_protocol_ids:
            component_excluded += 1
            if source_protocol_ids == {protocol_id}:
                component_excluded_current_only += 1
                continue
            component_excluded_mixed_source += 1
            item = dict(item)
            original_protocol_ids = list(item.get("source_protocol_ids") or [])
            original_protocol_names = list(item.get("source_protocol_names") or [])
            remaining_protocol_ids = [value for value in original_protocol_ids if str(value) != protocol_id]
            item["source_protocol_ids"] = remaining_protocol_ids
            if isinstance(item.get("source_protocol_names"), list) and len(original_protocol_names) == len(original_protocol_ids):
                item["source_protocol_names"] = [
                    name
                    for pid, name in zip(original_protocol_ids, original_protocol_names, strict=False)
                    if str(pid) != protocol_id
                ]
            item["protocol_count"] = max(1, int(item.get("protocol_count") or len(remaining_protocol_ids) or 1) - 1)
        filtered_component_edges.append(item)
    return {
        **memory,
        "oligo_nodes": filtered_nodes,
        "assembled_component_edges": filtered_component_edges,
        "leave_one_out": {
            "enabled": True,
            "protocol_id": protocol_id,
            "split": split,
            "excluded_current_protocol_rows": excluded,
            "excluded_current_protocol_only_rows": excluded_current_only,
            "excluded_current_protocol_mixed_source_rows": excluded_mixed_source,
            "excluded_current_component_rows": component_excluded,
            "excluded_current_component_only_rows": component_excluded_current_only,
            "excluded_current_component_mixed_source_rows": component_excluded_mixed_source,
        },
    }


def _memory_groups_by_name(memory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict):
            continue
        if not item.get("allowed_for_memory_completion", True):
            continue
        if not item.get("sequence"):
            continue
        direction = str(item.get("direction") or "unknown")
        keys = [display_name_key(str(item.get("name") or ""))]
        keys.extend(display_name_key(str(alias)) for alias in item.get("aliases") or [])
        for key in sorted({value for value in keys if value}):
            dedupe_key = (key, direction, semantic_sequence_key(str(item.get("sequence") or ""), str(item.get("name") or "")))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            grouped.setdefault(key, []).append(item)
    return grouped


def memory_by_name(memory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: items[0] for key, items in _memory_groups_by_name(memory).items() if len(items) == 1}


def memory_by_semantic_sequence(memory: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict):
            continue
        sequence = str(item.get("sequence") or "")
        if not sequence or not item.get("allowed_for_memory_completion", True):
            continue
        key = (str(item.get("direction") or "unknown"), semantic_sequence_key(sequence, str(item.get("name") or "")))
        if key[1]:
            grouped.setdefault(key, []).append(item)
    return {key: items[0] for key, items in grouped.items() if len(items) == 1}


def _memory_item_by_id(memory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for item in memory.get("oligo_nodes") or []:
        if isinstance(item, dict):
            item_id = _memory_item_id(item)
            if item_id:
                items[item_id] = item
    return items


def _agentic_memory_match_for_name(name: str, memory: dict[str, Any]) -> dict[str, Any] | None:
    exact = memory_by_name(memory).get(display_name_key(name))
    if exact:
        return exact
    name_key = display_name_key(name)
    compact = re.sub(r"[^a-z0-9]+", "", name.lower())
    tokens = set(name_key.split())
    acronym_patterns = {
        "ra3": r"\bra3\b",
        "ra5": r"\bra5\b",
        "rp1": r"\brp1\b",
        "rpi": r"\brpi\b",
        "rtp": r"\brtp\b",
    }
    wanted: set[str] = set()
    for token, pattern in acronym_patterns.items():
        if token in tokens or token in compact or re.search(pattern, name, flags=re.I):
            wanted.add(token)
    if "rpix" in compact:
        wanted.add("rpi")
    if "3 adapter" in name_key or "3 adaptor" in name_key or "3 adapter" in name.lower() or "3’ adapter" in name:
        wanted.add("ra3")
    if "5 adapter" in name_key or "5 adaptor" in name_key or "5 adapter" in name.lower() or "5’ adapter" in name:
        wanted.add("ra5")
    if not wanted:
        return None
    primary_matches: list[dict[str, Any]] = []
    alias_matches: list[dict[str, Any]] = []
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict) or not item.get("sequence") or not item.get("allowed_for_memory_completion", True):
            continue
        item_name = str(item.get("name") or "")
        alias_text = " ".join(str(alias) for alias in item.get("aliases") or [])
        for token in wanted:
            pattern = rf"\b{re.escape(token)}\b"
            if re.search(pattern, item_name, flags=re.I):
                primary_matches.append(item)
                break
            if alias_text and re.search(pattern, alias_text, flags=re.I):
                alias_matches.append(item)
                break
    matches = primary_matches or alias_matches
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in matches:
        key = (
            display_name_key(str(item.get("name") or "")),
            semantic_sequence_key(str(item.get("sequence") or ""), str(item.get("name") or "")),
        )
        unique.setdefault(key, item)
    return next(iter(unique.values())) if len(unique) == 1 else None


def memory_trace(memory: dict[str, Any]) -> dict[str, Any]:
    groups = _memory_groups_by_name(memory)
    ambiguous = sorted(key for key, items in groups.items() if len(items) > 1)
    return {
        "source": memory.get("source"),
        "source_format": memory.get("source_format"),
        "oligo_node_count": len(memory.get("oligo_nodes") or []),
        "component_memory_source": memory.get("component_memory_source"),
        "assembled_component_edge_count": len(memory.get("assembled_component_edges") or []),
        "completion_name_count": len(groups) - len(ambiguous),
        "ambiguous_name_count": len(ambiguous),
        "ambiguous_name_keys": ambiguous,
        "leave_one_out": memory.get("leave_one_out"),
    }


def memory_prompt_tsv(memory: dict[str, Any], max_rows: int = 400) -> str:
    columns = [str(column) for column in memory.get("memory_columns") or [] if str(column)]
    if not columns:
        columns = ["memory_id", "oligo_name", "direction", "oligo_sequence", "protocol_count"]
    rows: list[dict[str, object]] = []
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict) or not item.get("sequence"):
            continue
        row = dict(item)
        row.setdefault("oligo_name", item.get("name") or "")
        row.setdefault("oligo_sequence", item.get("sequence") or "")
        if "source_protocol_ids" in row and isinstance(row["source_protocol_ids"], list):
            row["source_protocol_ids"] = ";".join(str(value) for value in row["source_protocol_ids"])
        if "source_protocol_names" in row and isinstance(row["source_protocol_names"], list):
            row["source_protocol_names"] = ";".join(str(value) for value in row["source_protocol_names"])
        if "aliases" in row and isinstance(row["aliases"], list):
            row["aliases"] = ";".join(str(value) for value in row["aliases"])
        rows.append(row)
    if not rows:
        return "(none)"
    rows = sorted(rows, key=lambda row: (str(row.get("oligo_name") or row.get("name") or "").lower(), str(row.get("direction") or ""), str(row.get("oligo_sequence") or row.get("sequence") or "")))
    if len(rows) > max_rows:
        rows = rows[:max_rows]
    return write_tsv(columns, rows).strip()


def _component_edge_parent_is_detectable_assembled(edge: dict[str, Any]) -> bool:
    parent_name_key = display_name_key(str(edge.get("parent_oligo_name") or ""))
    if parent_name_key in {
        "illumina p5",
        "illumina p7",
        "t7 promoter",
        "nextera n s5xx entry point s5",
        "nextera n7xx entry point s7",
        "nextera tn5 binding site 19 bp mosaic end me",
    }:
        return False
    parent_sequence = str(edge.get("parent_sequence") or "")
    component_sequence = str(edge.get("component_sequence") or "")
    parent_key = sequence_key(parent_sequence)
    component_key = sequence_key(component_sequence)
    if not parent_key or not component_key:
        return False
    if "[" in parent_sequence or "]" in parent_sequence:
        return True
    return len(parent_key) >= 32 and len(parent_key) >= len(component_key) + 8


def _component_edge_parent_supported_by_prompt_source(
    edge: dict[str, Any],
    source_text_key: str,
    sequence_candidates: list[dict[str, Any]] | None,
) -> bool:
    if not _component_edge_parent_is_detectable_assembled(edge):
        return False
    parent_name = str(edge.get("parent_oligo_name") or "")
    parent_name_key = display_name_key(parent_name)
    if parent_name_key and parent_name_key in source_text_key:
        return True
    parent_key = support_sequence_key(str(edge.get("parent_sequence") or ""), parent_name)
    if not parent_key:
        return False
    for candidate in sequence_candidates or []:
        candidate_sequence = str(candidate.get("normalized_sequence") or candidate.get("raw_sequence") or "")
        candidate_context = " ".join(
            str(candidate.get(key) or "")
            for key in ["name_hint", "nearby_text", "quote"]
        )
        candidate_key = support_sequence_key(candidate_sequence, candidate_context)
        if parent_key and candidate_key and (parent_key == candidate_key or parent_key in candidate_key):
            return True
    return False


def component_memory_prompt_tsv(
    memory: dict[str, Any],
    max_rows: int = 80,
    source_text_key: str = "",
    sequence_candidates: list[dict[str, Any]] | None = None,
) -> str:
    columns = [
        "parent_oligo_name",
        "parent_sequence",
        "component_name",
        "component_role",
        "component_sequence",
        "protocol_count",
    ]
    rows: list[dict[str, object]] = []
    for item in memory.get("assembled_component_edges") or []:
        if not isinstance(item, dict):
            continue
        parent_name = str(item.get("parent_oligo_name") or "")
        component_name = str(item.get("component_name") or "")
        component_sequence = str(item.get("component_sequence") or "")
        if not parent_name or not component_name or not component_sequence:
            continue
        if source_text_key and not _component_edge_parent_supported_by_prompt_source(item, source_text_key, sequence_candidates):
            continue
        if source_text_key and not _component_edge_context_allowed(item, source_text_key):
            continue
        rows.append(item)
    if not rows:
        return "(none)"
    rows_by_parent: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            display_name_key(str(row.get("parent_oligo_name") or "")),
            support_sequence_key(str(row.get("parent_sequence") or ""), str(row.get("parent_oligo_name") or "")),
        )
        rows_by_parent.setdefault(key, []).append(row)
    rows = []
    for parent_rows in rows_by_parent.values():
        rows.extend(_filter_overlapping_component_edges(parent_rows, source_text_key, memory.get("assembled_component_edges") or []))
    rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("parent_oligo_name") or "").lower(),
            str(row.get("component_order") or ""),
            str(row.get("component_name") or "").lower(),
        ),
    )
    if len(rows) > max_rows:
        rows = rows[:max_rows]
    return write_tsv(columns, rows).strip()


def infer_role(name: str) -> str:
    text = name.lower()
    for role, terms in ROLE_TERMS.items():
        if any(term in text for term in terms):
            return role
    return "unknown"


def infer_kind(name: str, sequence: str | None, component_count: int = 0) -> str:
    text = name.lower()
    if "hairpin" in text:
        return "hairpin"
    if "double" in text and ("adapter" in text or "adaptor" in text):
        return "double_stranded"
    if component_count > 1:
        return "double_stranded"
    if re.search(r"\b(beads-tso|library pcr primer|poly-dt rt primer|illumina truseq read 1 primer)\b", text):
        return "assembled"
    if sequence and (
        re.search(r"\[[^\]]*(barcode|umi|index|bp)[^\]]*\]", sequence, flags=re.I)
        or re.search(r"[BUI]{6,}", sequence)
        or re.search(r"T{10,}V?N?$", sequence)
    ):
        return "assembled"
    return "single"


def _placeholder_component(length: int, role: str) -> OligoComponent:
    char = {"cell_barcode": "B", "umi": "U", "sample_index": "I", "barcode": "B", "rt_barcode": "B"}.get(role, "N")
    label = {
        "cell_barcode": "cell barcode",
        "umi": "UMI",
        "sample_index": "sample index",
        "barcode": "barcode",
        "rt_barcode": "RT barcode",
    }.get(role, "variable bases")
    sequence = char * length
    return OligoComponent(order=0, name=f"[{length}-bp {label}]", sequence=sequence, role=role)


def _role_for_placeholder(label: str, context: str, ordinal: int, length: int) -> str | None:
    label_text = label.lower()
    context_text = context.lower()
    is_barcoded_rt_primer = bool(
        re.search(r"\bbarcoded rt\b|\banchored oligo[- ]?dt\b|\boligo[- ]?dt\b|\brt barcode\b|\brt index\b", context_text)
        and re.search(r"t{10,}v?n?", context_text)
    )
    if is_barcoded_rt_primer:
        if re.fullmatch(r"n\d+|n{6,}", label_text, flags=re.I) and length in {6, 8, 10, 12}:
            return "umi"
        if "rt barcode" in label_text or "rt index" in label_text:
            return "rt_barcode"
        if "index" in label_text and length in {6, 8, 10, 12}:
            return "rt_barcode"
    if "rt barcode" in label_text or "rt index" in label_text:
        return "rt_barcode"
    if "cell barcode" in label_text or "10x barcode" in label_text:
        return "cell_barcode"
    if "umi" in label_text:
        return "umi"
    if "sample index" in label_text or re.search(r"\bi[57]\b|\bindex\b", label_text):
        return "sample_index"
    if "barcode" in label_text:
        return "cell_barcode" if ordinal == 0 and length >= 12 else "barcode"
    if "umi" in context_text and ordinal > 0 and length in {6, 8, 10, 12}:
        return "umi"
    if re.search(r"\b(sample index|i[57]|index)\b", context_text) and length in {6, 8, 10, 12}:
        return "sample_index"
    if "barcode" in context_text and ordinal == 0 and length >= 12:
        return "cell_barcode"
    if "gel bead" in context_text or "beads-tso" in context_text or "tttcttatat" in context_text:
        if ordinal == 0 and length in {14, 16}:
            return "cell_barcode"
        if ordinal == 1 and length in {10, 12}:
            return "umi"
    return None


def semantic_sequence_and_components(sequence: str | None, name: str = "", context: str = "") -> tuple[str | None, list[OligoComponent]]:
    if not sequence:
        return None, []
    normalized_sequence = normalize_sequence(sequence)
    combined_context = f"{name} {context} {normalized_sequence}"
    components: list[OligoComponent] = []
    seen_components: set[tuple[str, str]] = set()
    ordinal = 0

    def add_component(length: int, role: str) -> str:
        component = _placeholder_component(length, role)
        key = (component.sequence or "", role)
        if key not in seen_components:
            seen_components.add(key)
            components.append(component)
        return component.sequence or ("N" * length)

    def variable_repl(match: re.Match[str]) -> str:
        nonlocal ordinal
        token = match.group(0)
        if token.startswith("["):
            length_match = re.search(r"(\d+)\s*[- ]?\s*bp", token, flags=re.I)
            length = int(length_match.group(1)) if length_match else 0
        else:
            digit_match = re.fullmatch(r"N(\d+)", token, flags=re.I)
            length = int(digit_match.group(1)) if digit_match else len(token)
        role = _role_for_placeholder(token, combined_context, ordinal, length)
        ordinal += 1
        if length and role:
            return add_component(length, role)
        if token.startswith("["):
            return token
        return "N" * length

    value = re.sub(r"\[[^\]]+\]|N\d+|N{6,}", variable_repl, normalized_sequence, flags=re.I)
    value = value.replace("-", "")
    if re.search(r"T{10,}V?N?", value):
        component = OligoComponent(order=0, name="polyT", sequence="polyT", role="polyT")
        if ("polyT", "polyT") not in seen_components:
            components.append(component)
    for index, component in enumerate(components, start=1):
        component.order = index
    return value, components


def semantic_sequence_key(sequence: str | None, name: str = "", context: str = "") -> str:
    semantic, _components = semantic_sequence_and_components(sequence, name=name, context=context)
    return sequence_key(semantic)


def sequence_without_variable_placeholders(sequence: str | None, name: str = "", context: str = "") -> str:
    semantic, _components = semantic_sequence_and_components(sequence, name=name, context=context)
    return re.sub(r"[BUI]+|N{6,}", "", sequence_key(semantic))


def support_sequence_key(sequence: str | None, name: str = "", context: str = "") -> str:
    return semantic_sequence_key(sequence, name=name, context=context).replace("U", "T")


def support_sequence_without_variable_placeholders(sequence: str | None, name: str = "", context: str = "") -> str:
    return sequence_without_variable_placeholders(sequence, name=name, context=context).replace("U", "T")


def canonicalize_sequence_context_name(name: str, sequence: str | None) -> str:
    compact = sequence_key(sequence).lower()
    text = name.lower()
    if "oligo-dt" in text and ("tttttttttt" in compact or "barcode" in compact or "umi" in compact):
        return "Barcoded RT primer"
    if re.search(r"\b(?:indexed\s+)?p5\s+primer\b", text):
        return "PCR P5 primer"
    if re.search(r"\bp7\s+primer\b", text) and "[i7]" in (sequence or ""):
        return "Nextera N7 index primer"
    return name


def is_extractable_unsequenced_name(name: str) -> bool:
    text = name.lower()
    if looks_like_sequence_name(name) or re.search(r"[35]\s*['’′ʹ]", name):
        return False
    if re.search(r"\b(thaw|vortex|centrifuge|verify|precipitate|prepare|add|pipette|incubat|cleanup|hold|choose|record|pooled|contain|contains|used in)\b", text):
        return False
    if re.search(r"\b\d+(?:\.\d+)?\b.*\b\d+(?:\.\d+)?\b", text) and not re.search(r"\bpn[- ]?\d+\b", text):
        return False
    if text.strip() in {"tso", "p5", "p7", "read 1", "read 2", "primer", "adapter", "adaptor", "oligo"}:
        return False
    if "adapter" in text or "adaptor" in text:
        if re.search(r"\b(hemocytometer|tube|cylinder|scanner|tray|cartridge|holder|stand|reader|instrument|pcr strip|plate)\b", text):
            return False
    if not EXTRACTABLE_UNSEQUENCED_NAME_RE.search(name):
        return False
    if " kit" in text and not re.search(r"\b(primer|adapter|adaptor|oligo)\b", text):
        return False
    if len(name) > 100 and not re.search(r"\b(p5|p7|oligo-dt|tso)\b", text):
        return False
    return True


def should_keep_unsequenced_link(name: str, memory_lookup: dict[str, dict[str, Any]]) -> bool:
    name_key = display_name_key(name)
    if not name_key:
        return False
    if name_key in memory_lookup:
        return True
    return is_extractable_unsequenced_name(name)


def name_has_vdj_context(name: str) -> bool:
    return bool(re.search(r"\b(vdj|v\(d\)j|reverse outer primer|reverse inner primer|t/b mix|tcr|bcr)\b", name, flags=re.I))


def is_construct_or_product_name(name: str) -> bool:
    text = name.lower()
    if re.search(r"\blibrary pcr primer\b", text):
        return False
    return bool(
        re.search(
            r"\b("
            r"construct|final library|partial read|amplified product|amplification product|"
            r"sample index pcr product|ligation product|library product|product"
            r")\b",
            text,
        )
    )


def is_generic_directional_primer_name(name: str) -> bool:
    return bool(re.fullmatch(r"(?:pcr\s+)?(?:forward|reverse)\s+primer", name.strip(), flags=re.I))


def _append_note(notes: str | None, value: str) -> str:
    if not notes:
        return value
    if value in notes:
        return notes
    return f"{notes} {value}"


def assay_context_for_link(
    name: str,
    candidate: dict[str, Any] | None = None,
    sequence_repeat_count: int = 1,
) -> str:
    if name_has_vdj_context(name):
        return "vdj"
    if candidate:
        section = str((candidate.get("evidence") or {}).get("section") or "")
        quote = str((candidate.get("evidence") or {}).get("quote") or "")
        context = f"{section} {candidate.get('nearby_text') or ''} {quote}"
        if re.search(r"\b(vdj|v\(d\)j|reverse outer primer|reverse inner primer|tcr|bcr)\b", context, flags=re.I):
            return "vdj"
        if is_generic_directional_primer_name(name) and sequence_repeat_count > 2:
            return "vdj"
    return "gex"


def annotate_assay_note(notes: str | None, assay: str) -> str:
    return _append_note(notes, f"assay={assay}")


def is_vdj_oligo(oligo: Oligo) -> bool:
    return name_has_vdj_context(oligo.name) or "assay=vdj" in str(oligo.notes or "").lower()


def looks_like_sequence_name(name: str) -> bool:
    compact = re.sub(r"\s+", "", name)
    if re.search(r"[ACGTURYSWKMBDHVN]{12,}", compact, flags=re.I):
        return True
    if re.search(r"\[[^\]]*(?:bp|barcode|umi|index)[^\]]*\]", compact, flags=re.I):
        return True
    return False


def sequence_components(sequence: str | None) -> list[OligoComponent]:
    if not sequence:
        return []
    _semantic, semantic_components = semantic_sequence_and_components(sequence)
    if semantic_components:
        return semantic_components
    components: list[OligoComponent] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[[^\]]+\]", sequence):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        role = "cell_barcode" if "cell barcode" in token.lower() else "umi" if "umi" in token.lower() else "sample_index" if "index" in token.lower() else "barcode"
        components.append(OligoComponent(order=len(components) + 1, name=token, sequence=token, role=role))
    if re.search(r"T{10,}V?N?", sequence):
        components.append(OligoComponent(order=len(components) + 1, name="polyT", sequence="polyT", role="polyT"))
    return components


def extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    fence = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", text)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        try:
            parsed = json.loads(brace.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    return None


def clip_text(text: str, max_chars: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half].rstrip()} ... {text[-half:].lstrip()}"


def content_tags(block: dict[str, Any], text: str) -> list[str]:
    tags: set[str] = set()
    block_type = str(block.get("block_type") or "unknown")
    if block_type in {"table", "sequence_diagram", "caption", "warning", "note"}:
        tags.add(block_type)
    lower = text.lower()
    if EXTRACTABLE_UNSEQUENCED_NAME_RE.search(text):
        tags.add("oligo_name")
    if re.search(r"[35]\s*['’′ʹ]|/[35][A-Za-z]+/|\[[^\]]*(barcode|umi|index|bp)[^\]]*\]|[ACGTURYSWKMBDHVN]{12,}", text, re.I):
        tags.add("sequence_candidate")
    if re.search(r"\b(reagent|buffer|enzyme|kit|primer|adapter|oligo|idt|illumina|nextera|truseq)\b", lower):
        tags.add("reagent_or_oligo_context")
    if re.search(r"\b(incubat|mix|wash|spin|centrifug|amplification|pcr|reverse transcription|tagmentation)\b", lower):
        tags.add("procedure")
    if re.search(r"\b(read 1|read 2|index 1|index 2|sequenc|nextseq|miseq|novaseq)\b", lower):
        tags.add("sequencing")
    return sorted(tags)


def split_block_text(text: str, max_chars: int = 2500) -> list[tuple[int, int, str]]:
    if len(text) <= max_chars:
        return [(0, len(text), text)]
    parts: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start + max_chars // 2:
                end = newline
            else:
                space = text.rfind(" ", start, end)
                if space > start + max_chars // 2:
                    end = space
        chunk_text = text[start:end].strip()
        if chunk_text:
            parts.append((start, end, chunk_text))
        start = max(end, start + 1)
    return parts


def build_evidence_chunks(blocks: list[dict[str, Any]], max_chars: int = 2500) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block.get("text") or "")
        for start, end, chunk_text in split_block_text(text, max_chars=max_chars):
            chunks.append(
                {
                    "chunk_id": f"chunk_{len(chunks) + 1:05d}",
                    "block_ids": [block.get("block_id")],
                    "source_id": block.get("source_id"),
                    "page": block.get("page"),
                    "section": block.get("section"),
                    "block_type": block.get("block_type"),
                    "char_range": [start, end],
                    "tags": content_tags(block, chunk_text),
                    "text_preview": clip_text(chunk_text, 300),
                    "text": chunk_text,
                }
            )
    return chunks


def chunk_index(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": chunk.get("chunk_id"),
            "block_ids": chunk.get("block_ids"),
            "source_id": chunk.get("source_id"),
            "page": chunk.get("page"),
            "section": chunk.get("section"),
            "block_type": chunk.get("block_type"),
            "tags": chunk.get("tags"),
            "text_preview": chunk.get("text_preview"),
        }
        for chunk in chunks
    ]


def relevant_chunk_index(chunks: list[dict[str, Any]], context_chunks: list[dict[str, Any]], max_chunks: int = 140) -> list[dict[str, Any]]:
    selected_ids: list[str] = []
    seen: set[str] = set()
    for chunk in context_chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if chunk_id and chunk_id not in seen:
            seen.add(chunk_id)
            selected_ids.append(chunk_id)
    for chunk in chunks:
        tags = set(chunk.get("tags") or [])
        if not tags.intersection({"sequence_candidate", "sequence_diagram", "oligo_name", "reagent_or_oligo_context"}):
            continue
        chunk_id = str(chunk.get("chunk_id") or "")
        if chunk_id and chunk_id not in seen:
            seen.add(chunk_id)
            selected_ids.append(chunk_id)
        if len(selected_ids) >= max_chunks:
            break
    by_id = {str(chunk.get("chunk_id")): chunk for chunk in chunks}
    relevant = chunk_index([by_id[chunk_id] for chunk_id in selected_ids if chunk_id in by_id])
    for item in relevant:
        item.pop("text_preview", None)
    return relevant


def context_chunk_refs(context_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": chunk.get("chunk_id"),
            "block_ids": chunk.get("block_ids"),
            "source_id": chunk.get("source_id"),
            "page": chunk.get("page"),
            "section": chunk.get("section"),
            "block_type": chunk.get("block_type"),
            "tags": chunk.get("tags"),
        }
        for chunk in context_chunks
    ]


def find_chunk_ids(chunks: list[dict[str, Any]], block_id: str | None, quote: str | None = None, limit: int = 3) -> list[str]:
    if not block_id:
        return []
    matches = [chunk for chunk in chunks if block_id in {str(value) for value in chunk.get("block_ids") or []}]
    if quote:
        normalized_quote = re.sub(r"\s+", " ", quote).strip()
        if normalized_quote:
            direct = [
                chunk
                for chunk in matches
                if normalized_quote[:80] in re.sub(r"\s+", " ", str(chunk.get("text") or ""))
                or str(chunk.get("text") or "") in quote
            ]
            if direct:
                matches = direct
    return [str(chunk.get("chunk_id")) for chunk in matches[:limit] if chunk.get("chunk_id")]


def compact_evidence(evidence: Any, quote_chars: int = 300) -> dict[str, Any] | None:
    if not isinstance(evidence, dict):
        return None
    compact = {
        "source_id": evidence.get("source_id"),
        "page": evidence.get("page"),
        "section": evidence.get("section"),
    }
    if quote_chars > 0:
        compact["quote"] = clip_text(str(evidence.get("quote") or ""), quote_chars)
    return compact


def compact_name_mentions(name_mentions: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for mention in name_mentions:
        quote = str(mention.get("nearby_text") or "")
        compact.append(
            {
                "mention_id": mention.get("mention_id"),
                "name": mention.get("name"),
                "block_id": mention.get("block_id"),
                "chunk_ids": find_chunk_ids(chunks, str(mention.get("block_id") or ""), quote),
                "evidence": compact_evidence(mention.get("evidence"), quote_chars=0),
            }
        )
    return compact


def _candidate_prompt_score(candidate: dict[str, Any]) -> tuple[int, int, str]:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ["name_hint", "nearby_text", "raw_sequence", "normalized_sequence"]
    )
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    source_id = str(evidence.get("source_id") or "")
    section = str(evidence.get("section") or "")
    lowered = " ".join([text, source_id, section]).lower()
    sequence = sequence_key(str(candidate.get("normalized_sequence") or ""))
    score = 0
    if re.search(r"\b(primer|adapter|adaptor|oligo|truseq|nextera|tn5|mosaic|sbs|ume|lna|pcr|read\s*[12]|index|i[57]|p[57])\b", lowered):
        score += 40
    if re.search(r"supp(?:lementary)?table[1-5]\b|supptable[1-5]\b|supplementary table [1-5]\b", section, flags=re.I):
        score += 35
    if source_id.lower().endswith((".xlsx", ".tsv", ".csv")) and re.search(r"\b(oligo|primer|adapter|sequence|index)\b", lowered):
        score += 15
    if re.search(r"\b(sbs12|u[-_ ]?me|pcr[_ -]?i[57]|pcr[_ -]?a[_ -]?i5|flowcell|nextera[_ -]?a14)\b", lowered, flags=re.I):
        score += 25
    if re.search(r"/(?:5phos|ideoxyu|3invdt|phos)/|\+|\[|\]", str(candidate.get("normalized_sequence") or ""), flags=re.I):
        score += 15
    if 12 <= len(sequence) <= 120:
        score += 10
    if re.search(r"\b(supp(?:lementary)?table6|supptable6|seurat|hg38|mm10|cell_type|umap|object|human|mouse)\b", lowered):
        score -= 90
    if str(candidate.get("nearby_text") or "").count("\t") >= 12:
        score -= 25
    return score, len(sequence), str(candidate.get("candidate_id") or "")


def select_prompt_sequence_candidates(
    sequence_candidates: list[dict[str, Any]],
    max_candidates: int = 700,
) -> list[dict[str, Any]]:
    if len(sequence_candidates) <= max_candidates:
        return sequence_candidates
    ranked = sorted(sequence_candidates, key=_candidate_prompt_score, reverse=True)
    return ranked[:max_candidates]


def _mention_prompt_score(mention: dict[str, Any]) -> tuple[int, str]:
    text = " ".join(str(mention.get(key) or "") for key in ["name", "nearby_text"])
    lowered = text.lower()
    score = 0
    if re.search(r"\b(primer|adapter|adaptor|oligo|truseq|nextera|tn5|mosaic|sbs|ume|lna|pcr|read\s*[12]|index|i[57]|p[57])\b", lowered):
        score += 25
    if is_extractable_unsequenced_name(str(mention.get("name") or "")):
        score += 10
    if re.search(r"\b(produce|efficiency|strategy|species|crosstalk|performed|during|through)\b", lowered):
        score -= 15
    return score, str(mention.get("mention_id") or "")


def select_prompt_name_mentions(name_mentions: list[dict[str, Any]], max_mentions: int = 250) -> list[dict[str, Any]]:
    if len(name_mentions) <= max_mentions:
        return name_mentions
    ranked = sorted(name_mentions, key=_mention_prompt_score, reverse=True)
    return ranked[:max_mentions]


def chunk_manifest(chunks: list[dict[str, Any]], max_groups: int = 60) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for chunk in chunks:
        key = (str(chunk.get("source_id") or ""), str(chunk.get("section") or ""))
        group = groups.setdefault(
            key,
            {
                "source_id": key[0],
                "section": key[1] or None,
                "chunk_count": 0,
                "chunk_id_examples": [],
                "tags": set(),
            },
        )
        group["chunk_count"] += 1
        if len(group["chunk_id_examples"]) < 4 and chunk.get("chunk_id"):
            group["chunk_id_examples"].append(chunk.get("chunk_id"))
        group["tags"].update(str(tag) for tag in chunk.get("tags") or [])
    rows: list[dict[str, Any]] = []
    for group in groups.values():
        rows.append(
            {
                **group,
                "tags": sorted(group["tags"]),
            }
        )
    return sorted(rows, key=lambda row: (str(row["source_id"]), str(row.get("section") or "")))[:max_groups]


def candidate_section_index(sequence_candidates: list[dict[str, Any]], max_groups: int = 120) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in sequence_candidates:
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        key = (str(evidence.get("source_id") or ""), str(evidence.get("section") or ""))
        group = groups.setdefault(
            key,
            {
                "source_id": key[0],
                "section": key[1] or None,
                "candidate_count": 0,
                "candidate_id_examples": [],
            },
        )
        group["candidate_count"] += 1
        if len(group["candidate_id_examples"]) < 8 and candidate.get("candidate_id"):
            group["candidate_id_examples"].append(candidate.get("candidate_id"))
    return sorted(groups.values(), key=lambda row: (str(row["source_id"]), str(row.get("section") or "")))[:max_groups]


def compact_sequence_candidates(sequence_candidates: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for candidate in sequence_candidates:
        quote = str(candidate.get("nearby_text") or "")
        compact.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "block_id": candidate.get("block_id"),
                "chunk_ids": find_chunk_ids(chunks, str(candidate.get("block_id") or ""), quote),
                "normalized_sequence": candidate.get("normalized_sequence"),
                "direction": candidate.get("direction"),
                "name_hint": candidate.get("name_hint"),
                "evidence": compact_evidence(candidate.get("evidence"), quote_chars=0),
            }
        )
    return compact


def linker_context_blocks(
    blocks: list[dict[str, Any]],
    name_mentions: list[dict[str, Any]],
    sequence_candidates: list[dict[str, Any]],
    neighbor_radius: int = 1,
) -> list[dict[str, Any]]:
    block_ids = {str(item.get("block_id")) for item in name_mentions}
    block_ids.update(str(item.get("block_id")) for item in sequence_candidates)
    by_id = {str(block.get("block_id")): index for index, block in enumerate(blocks)}
    selected: set[int] = set()
    for block_id in block_ids:
        index = by_id.get(block_id)
        if index is None:
            continue
        for nearby in range(max(0, index - neighbor_radius), min(len(blocks), index + neighbor_radius + 1)):
            selected.add(nearby)
    return [
        {
            "block_id": blocks[index].get("block_id"),
            "source_id": blocks[index].get("source_id"),
            "page": blocks[index].get("page"),
            "section": blocks[index].get("section"),
            "block_type": blocks[index].get("block_type"),
            "text": blocks[index].get("text"),
        }
        for index in sorted(selected)
    ]


def linker_context_chunks(
    chunks: list[dict[str, Any]],
    name_mentions: list[dict[str, Any]],
    sequence_candidates: list[dict[str, Any]],
    max_chunks: int = 80,
    max_text_chars: int = 1000,
) -> list[dict[str, Any]]:
    selected_ids: list[str] = []
    seen: set[str] = set()
    for mention in name_mentions:
        for chunk_id in find_chunk_ids(chunks, str(mention.get("block_id") or ""), str(mention.get("nearby_text") or "")):
            if chunk_id not in seen:
                seen.add(chunk_id)
                selected_ids.append(chunk_id)
    for candidate in sequence_candidates:
        for chunk_id in find_chunk_ids(chunks, str(candidate.get("block_id") or ""), str(candidate.get("nearby_text") or "")):
            if chunk_id not in seen:
                seen.add(chunk_id)
                selected_ids.append(chunk_id)
    by_id = {str(chunk.get("chunk_id")): chunk for chunk in chunks}
    context: list[dict[str, Any]] = []
    for chunk_id in selected_ids[:max_chunks]:
        chunk = by_id.get(chunk_id)
        if not chunk:
            continue
        context.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "block_ids": chunk.get("block_ids"),
                "source_id": chunk.get("source_id"),
                "page": chunk.get("page"),
                "section": chunk.get("section"),
                "block_type": chunk.get("block_type"),
                "tags": chunk.get("tags"),
                "text": clip_text(str(chunk.get("text") or ""), max_text_chars),
            }
        )
    return context


def deterministic_links(name_mentions: list[dict[str, Any]], sequence_candidates: list[dict[str, Any]], memory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    candidate_link_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_names: set[str] = set()
    memory_lookup = memory_by_name(memory or empty_memory())
    for candidate in sequence_candidates:
        candidate_id = str(candidate["candidate_id"])
        sequence = str(candidate.get("normalized_sequence") or "")
        raw_name = str(candidate.get("name_hint") or f"Sequence candidate {candidate_id}").strip()
        name = canonicalize_sequence_context_name(raw_name, sequence)
        if re.fullmatch(r"Sequence candidate \d+", name):
            continue
        used_names.add(display_name_key(name))
        candidate_link_pairs.append(
            (
                candidate,
                {
                    "mention_id": None,
                    "name": name,
                    "candidate_id": candidate_id,
                    "role": infer_role(name),
                    "notes": "Linked by deterministic sequence context.",
                },
            )
        )

    meaningful_sequence_keys = {
        sequence_key(candidate.get("normalized_sequence"))
        for candidate, link in candidate_link_pairs
        if not looks_like_sequence_name(str(link.get("name") or ""))
    }
    links: list[dict[str, Any]] = []
    seen_candidate_pairs: set[tuple[str, str]] = set()
    for candidate, link in candidate_link_pairs:
        seq_key = sequence_key(candidate.get("normalized_sequence"))
        name = str(link.get("name") or "")
        if looks_like_sequence_name(name) and seq_key in meaningful_sequence_keys:
            continue
        pair = (display_name_key(name), seq_key)
        if pair in seen_candidate_pairs:
            continue
        seen_candidate_pairs.add(pair)
        links.append(link)

    for mention in name_mentions:
        name = str(mention.get("name") or "").strip()
        name_key = display_name_key(name)
        memory_match = _agentic_memory_match_for_name(name, memory or empty_memory())
        if not name_key or name_key in used_names or (not memory_match and not should_keep_unsequenced_link(name, memory_lookup)):
            continue
        used_names.add(name_key)
        links.append(
            {
                "mention_id": mention["mention_id"],
                "name": name,
                "candidate_id": None,
                "role": infer_role(name),
                "notes": "Named oligo term found without an explicit sequence candidate.",
            }
        )
    return links


def codex_link_prompt(
    protocol_id: str,
    protocol_name: str,
    name_mentions: list[dict[str, Any]],
    sequence_candidates: list[dict[str, Any]],
    indexed_chunks: list[dict[str, Any]],
    context_chunks: list[dict[str, Any]],
    chunks_overview: list[dict[str, Any]] | None = None,
    sequence_candidate_sections: list[dict[str, Any]] | None = None,
    source_file_paths: list[str] | None = None,
    memory_context_tsv: str = "(none)",
    component_memory_context_tsv: str = "(none)",
    chunks_json_path: str | None = None,
) -> str:
    return f"""You are Codex reviewing cDNA oligo extraction evidence.

Goal:
- Link oligo/adaptor/adapter/primer names to deterministic sequence candidate IDs.
- If the deterministic scanner missed an oligo name or sequence that is visibly present in chunks_json_path or source_file_paths, extract it from the source text and return it as an additional_sequence_candidate.
- Also write a compact protocol brief from the source documents so later review understands the technique context.

Hard rules:
- Never invent sequence strings.
- Existing links must use candidate_id values from sequence_candidates, or null when no exact sequence is shown.
- For double-stranded adapters, return one link with kind=double_stranded, candidate_id=null, and component_candidate_ids for the strand sequence candidates.
- Return unique oligo / adapter / primer extraction records. Prefer the canonical oligo name and one record per actual oligo when the source describes one oligo; primer pools may contain multiple distinct primers.
- Do not emit final library constructs, amplified products, partial read cDNA constructs, schematic products, or workflow diagrams as oligos unless the source identifies them as a distinct synthesized oligo/adapter/primer.
- Do not emit component-only strand records when the parent double-stranded adapter is emitted.
- If an oligo/primer belongs to a V(D)J/TCR/BCR primer pool, emit it as a primer and put "assay=vdj" in notes.
- If an oligo/primer belongs to the gene-expression library chemistry, put "assay=gex" in notes.
- For bare repeated section labels such as "Forward Primer" in a V(D)J primer-pool section, prefer a clearer name such as "VDJ Forward Primer" and put "assay=vdj" in notes.
- Additional candidates must copy raw_sequence exactly from chunks_json_path/source_file_paths and include chunk_id, block_id when available, plus quote.
- The inline chunk and candidate lists are compact seed indexes only, not the full evidence set. Inspect chunks_json_path or source_file_paths with tools when names, table rows, diagrams, or sequences are needed.
- training_oligo_memory_tsv is prior context only. It is not source evidence.
- Never copy a sequence from training_oligo_memory_tsv into additional_sequence_candidates.
- assembled_oligo_component_memory_tsv is prior component-relationship context only. It is not source evidence by itself.
- assembled_oligo_component_memory_tsv is directional: source-supported assembled parent -> fixed child component. Never invert it to infer parent oligos from child components.
- Use memory to canonicalize names, aliases, roles, kinds, and likely direction when source evidence supports the same oligo.
- Use assembled component memory to decompose source-supported assembled oligos into canonical component records. Example: if the source supports an assembled Illumina/Nextera/P5/P7 parent primer and component memory says that parent contains Read 1, Read 2, Tn5 ME, s5, or s7 components, return canonical links for those components with candidate_id=null and notes explaining the parent relationship.
- Do not emit parent oligos merely because a child component sequence is present. For example, a T7 promoter, P5 adapter, P7 adapter, Read 1 site, s5, s7, or Tn5 ME alone must not promote every assembled parent that contains it.
- Do not decompose final library/product constructs. Only decompose synthesized oligos/adapters/primers or standard kit primers/adapters supported by the source.
- Prefer canonical memory names over local labels. Example: a gel bead primer containing read 1 + 16-bp barcode + 10-bp UMI + TSO should be named Beads-TSO, role=oligo, kind=assembled.
- Resolve common kit acronyms agentically through memory when source mentions them:
  - "RNA RT Primer (RTP)" -> "Illumina RTP primer (TruSeq Small RNA kit)".
  - "RNA PCR Primer (RP1)" -> "Illumina RP1 primer (TruSeq Small RNA kit)".
  - "RNA PCR Primer (RPIX)" or "RPIX" -> "Illumina RPI primers".
  - "3' adapter (RA3)" -> "Illumina RA3 adapter (TruSeq Small RNA kit)".
  - "5' adapter (RA5)" -> "Illumina RA5 adapter (TruSeq Small RNA kit)".
- If source shows only the acronym/local name and memory has a unique canonical match, emit the canonical memory name with candidate_id=null; the resolver will complete the sequence from memory.
- Represent variable sequence roles semantically in notes/review if needed: cell barcodes are B repeated to the exact length, UMIs are U repeated to the exact length, and sample indexes are I repeated to the exact length.
- Do not emit reagent handling steps, section titles, volume rows, bare labels, or prose sentences as null-sequence oligos.
- Do not reverse-complement.
- Do not patch files. You may inspect chunks_json_path and source_file_paths read-only, and you may run read-only commands to parse tables or align visible sequence rows.
- Any sequence you emit must still be copied from chunks_json_path/source_file_paths or referenced from memory through candidate_id=null; analysis may help find/common-pattern rows but may not create biological sequence content from reasoning alone.
- Return final links from the provided deterministic candidates and chunks_json_path/source_file_paths. Do not request another round.
- The protocol_brief is context only, not oligo evidence. Do not emit oligos solely because they are expected by the brief.
- protocol_brief.summary should be biological and reader-facing: what biological measurement or question the technology enables, why it was developed, what is novel about the method, and important biological result/finding if visible in the chunks.
- protocol_brief.summary and protocol_brief.major_steps must never mention chunks, chunk IDs, scanner behavior, memory, prediction files, trace files, or any other internal pipeline mechanics.
- Keep protocol_brief.summary distinct from major_steps. Do not turn it into a protocol recap, adapter/primer list, sequencing layout, or chemistry inventory.
- protocol_brief.major_steps should be a concise ordered list of major experimental steps, not reagent handling minutiae.
- Internally use the protocol_brief you draft to guide which candidate links, canonical names, and memory aliases are plausible for this technique, but still require each emitted oligo link to be supported by chunks_json_path/source_file_paths or a source-supported memory rule.
- Return ONLY JSON. No markdown.

Return JSON:
{{
  "protocol_brief": {{
    "summary": "biological summary: what the technology measures/enables, motivation, novelty, and important result/finding",
    "major_steps": [
      "major step 1",
      "major step 2"
    ]
  }},
  "links": [
    {{
      "mention_id": "name_0001 or null",
      "name": "Template Switching Oligo",
      "candidate_id": "seq_0001 or codex_seq_0001 or null",
      "component_candidate_ids": [
        {{"candidate_id": "seq_0002", "role": "forward_strand"}},
        {{"candidate_id": "seq_0003", "role": "reverse_strand"}}
      ],
      "role": "primer|adapter|oligo|primer_site|tn5_binding_site|probe|promoter|unknown",
      "kind": "single|assembled|double_stranded|hairpin",
      "notes": null
    }}
  ],
  "additional_sequence_candidates": [
    {{
      "candidate_id": "codex_seq_0001",
      "name": "missing oligo name",
      "raw_sequence": "5'- ACGT -3'",
      "direction": "5_to_3|3_to_5|unknown",
      "chunk_id": "chunk_00001",
      "block_id": "block_0001",
      "quote": "source text containing the exact sequence",
      "role": "primer|adapter|oligo|primer_site|tn5_binding_site|probe|promoter|unknown",
      "kind": "single|assembled|double_stranded|hairpin",
      "notes": null
    }}
  ],
  "review_flags": []
}}

Protocol: {protocol_id} / {protocol_name}
chunks_json_path for offline review: {chunks_json_path or ""}
source_file_paths for source inspection:
{json.dumps(source_file_paths or [], indent=2, ensure_ascii=False)}

chunks_overview:
{json.dumps(chunks_overview or [], indent=2, ensure_ascii=False)}

sequence_candidate_sections:
{json.dumps(sequence_candidate_sections or [], indent=2, ensure_ascii=False)}

indexed_chunks:
{json.dumps(indexed_chunks, indent=2, ensure_ascii=False)}

source_context_chunk_refs:
{json.dumps(context_chunks, indent=2, ensure_ascii=False)}

name_mentions:
{json.dumps(name_mentions, indent=2, ensure_ascii=False)}

sequence_candidates:
{json.dumps(sequence_candidates, indent=2, ensure_ascii=False)}

training_oligo_memory_tsv:
{memory_context_tsv}

assembled_oligo_component_memory_tsv:
{component_memory_context_tsv}
"""


def append_codex_candidates(
    sequence_candidates: list[dict[str, Any]],
    parsed: dict[str, Any],
    context_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    chunk_lookup = {str(chunk.get("chunk_id")): chunk for chunk in context_chunks}
    block_lookup: dict[str, dict[str, Any]] = {}
    for chunk in context_chunks:
        for block_id in chunk.get("block_ids") or []:
            block_lookup[str(block_id)] = chunk
    valid_ids = {str(candidate.get("candidate_id")) for candidate in sequence_candidates}
    candidates = list(sequence_candidates)
    raw_items = parsed.get("additional_sequence_candidates")
    if not isinstance(raw_items, list):
        return candidates
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw_sequence = item.get("raw_sequence")
        if not isinstance(raw_sequence, str) or not raw_sequence.strip():
            continue
        block_id = str(item.get("block_id") or "")
        chunk_id = str(item.get("chunk_id") or "")
        chunk = chunk_lookup.get(chunk_id) or block_lookup.get(block_id)
        if not chunk:
            continue
        if not block_id:
            block_ids = chunk.get("block_ids") or []
            block_id = str(block_ids[0]) if block_ids else ""
        normalized = normalize_sequence(raw_sequence)
        if not sequence_key(normalized):
            continue
        candidate_id = str(item.get("candidate_id") or f"codex_seq_{len(candidates) + 1:04d}")
        if not candidate_id.startswith("codex_seq_"):
            candidate_id = f"codex_seq_{len(candidates) + 1:04d}"
        while candidate_id in valid_ids:
            candidate_id = f"codex_seq_{len(candidates) + 1:04d}"
        valid_ids.add(candidate_id)
        quote = str(item.get("quote") or chunk.get("text") or "").strip()
        evidence = Evidence(
            source_id=str(chunk.get("source_id") or ""),
            page=chunk.get("page"),
            section=chunk.get("section"),
            quote=quote or None,
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "block_id": block_id,
                "chunk_id": chunk.get("chunk_id"),
                "raw_sequence": raw_sequence.strip(),
                "normalized_sequence": normalized,
                "direction": item.get("direction") if item.get("direction") in {"5_to_3", "3_to_5", "unknown"} else infer_direction(raw_sequence),
                "nearby_text": quote,
                "name_hint": str(item.get("name") or f"Codex sequence candidate {candidate_id}"),
                "candidate_source": "codex_chunk_extracted",
                "evidence": evidence.model_dump(mode="json"),
            }
        )
    return candidates


def sanitize_links(
    raw_links: Any,
    sequence_candidates: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
    memory: dict[str, Any] | None = None,
    source_text_key: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(raw_links, list):
        return fallback
    valid_candidate_ids = {str(candidate["candidate_id"]) for candidate in sequence_candidates}
    memory_lookup = memory_by_name(memory or empty_memory())
    links = []
    seen_link_keys: set[tuple[str, str, str]] = set()
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        candidate_id = item.get("candidate_id")
        if candidate_id is not None and str(candidate_id) not in valid_candidate_ids:
            candidate_id = None
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_components = item.get("component_candidate_ids")
        component_candidate_ids = []
        if isinstance(raw_components, list):
            for component in raw_components:
                if not isinstance(component, dict):
                    continue
                component_id = str(component.get("candidate_id") or "")
                if component_id not in valid_candidate_ids:
                    continue
                component_role = str(component.get("role") or "")
                component_candidate_ids.append(
                    {
                        "candidate_id": component_id,
                        "role": component_role if component_role else "strand",
                    }
                )
        if not candidate_id and not component_candidate_ids and not should_keep_unsequenced_link(name, memory_lookup):
            continue
        if (
            not candidate_id
            and not component_candidate_ids
            and source_text_key
            and not _source_supports_memory_link(name, memory_lookup, source_text_key)
        ):
            continue
        link = {
            "mention_id": item.get("mention_id") if isinstance(item.get("mention_id"), str) else None,
            "name": name.strip(),
            "candidate_id": str(candidate_id) if candidate_id else None,
            "component_candidate_ids": component_candidate_ids,
            "role": item.get("role") if item.get("role") in set(ROLE_TERMS) | {"unknown"} else infer_role(name),
            "kind": item.get("kind") if item.get("kind") in {"single", "assembled", "double_stranded", "hairpin"} else None,
            "notes": item.get("notes") if isinstance(item.get("notes"), str) else None,
        }
        links.append(link)
        seen_link_keys.add((display_name_key(str(link["name"])), str(link["candidate_id"] or ""), json.dumps(link["component_candidate_ids"], sort_keys=True)))
    for fallback_link in fallback:
        if not isinstance(fallback_link, dict):
            continue
        fallback_name = str(fallback_link.get("name") or "")
        fallback_components = fallback_link.get("component_candidate_ids") or []
        key = (
            display_name_key(fallback_name),
            str(fallback_link.get("candidate_id") or ""),
            json.dumps(fallback_components, sort_keys=True),
        )
        if key in seen_link_keys:
            continue
        seen_link_keys.add(key)
        links.append(fallback_link)
    return links or fallback


def _source_supports_memory_link(name: str, memory_lookup: dict[str, dict[str, Any]], source_text_key: str) -> bool:
    name_key = display_name_key(name)
    if name_key and name_key in source_text_key:
        return True
    item = memory_lookup.get(name_key)
    if item:
        keys = [display_name_key(str(alias)) for alias in item.get("aliases") or []]
        keys.append(display_name_key(str(item.get("name") or "")))
        if any(key and key in source_text_key for key in keys):
            return True
    generic = {
        "adapter",
        "adaptor",
        "primer",
        "oligo",
        "illumina",
        "truseq",
        "nextera",
        "rna",
        "dna",
        "read",
        "kit",
        "small",
    }
    for token in name_key.split():
        if token in generic:
            continue
        if len(token) >= 3 and token in source_text_key:
            return True
        if token in {"p5", "p7", "i5", "i7"} and re.search(rf"\b{re.escape(token)}\b", source_text_key):
            return True
    return False


def sanitize_protocol_brief(raw_brief: Any) -> dict[str, Any]:
    if not isinstance(raw_brief, dict):
        return {"summary": None, "major_steps": []}

    internal_re = re.compile(
        r"\b(chunks?|chunk_id|source_context|scanner|deterministic|memory|trace|prediction(?:\.json)?|chunks\.json|ground[- ]truth|pipeline)\b",
        flags=re.I,
    )

    def scrub_internal_text(value: str) -> str | None:
        parts = re.split(r"(?<=[.!?])\s+", value)
        kept = [part.strip() for part in parts if part.strip() and not internal_re.search(part)]
        if not kept and not internal_re.search(value):
            kept = [value.strip()]
        cleaned = " ".join(kept).strip()
        return cleaned or None

    def clean_text(value: Any, max_chars: int) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = re.sub(r"\s+", " ", value).strip()
        cleaned = scrub_internal_text(cleaned) or ""
        if not cleaned:
            return None
        return cleaned[:max_chars].rstrip()

    raw_steps = raw_brief.get("major_steps")
    steps: list[str] = []
    if isinstance(raw_steps, list):
        for item in raw_steps:
            cleaned = clean_text(item, 160)
            if cleaned and cleaned not in steps:
                steps.append(cleaned)
            if len(steps) >= 12:
                break

    return {
        "summary": clean_text(raw_brief.get("summary"), 1200),
        "major_steps": steps,
    }


def run_linker(
    protocol_id: str,
    protocol_name: str,
    blocks: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    name_mentions: list[dict[str, Any]],
    sequence_candidates: list[dict[str, Any]],
    memory: dict[str, Any] | None = None,
    chunks_json_path: Path | None = None,
    source_file_list: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    memory_for_prompt = memory or empty_memory()
    fallback = deterministic_links(name_mentions, sequence_candidates, memory_for_prompt)
    load_env_local()
    prompt_mentions_source = select_prompt_name_mentions(
        [mention for mention in name_mentions if is_extractable_unsequenced_name(str(mention.get("name") or ""))]
    )
    prompt_sequence_source = select_prompt_sequence_candidates(sequence_candidates)
    context_chunks = linker_context_chunks(chunks, prompt_mentions_source, prompt_sequence_source, max_chunks=60, max_text_chars=700)
    indexed_chunks = relevant_chunk_index(chunks, context_chunks, max_chunks=140)
    prompt_name_mentions = compact_name_mentions(prompt_mentions_source, chunks)
    prompt_sequence_candidates = compact_sequence_candidates(prompt_sequence_source, chunks)
    chunks_overview = chunk_manifest(chunks)
    sequence_candidate_sections = candidate_section_index(sequence_candidates)
    source_text_key = display_name_key(
        " ".join(
            [
                *(str(chunk.get("text") or "") for chunk in context_chunks),
                *(str(mention.get("name") or "") for mention in prompt_name_mentions),
                *(
                    " ".join(
                        str(candidate.get(key) or "")
                        for key in ["name_hint", "nearby_text", "quote", "normalized_sequence"]
                    )
                    for candidate in prompt_sequence_candidates
                ),
            ]
        )
    )
    prompt = codex_link_prompt(
        protocol_id,
        protocol_name,
        prompt_name_mentions,
        prompt_sequence_candidates,
        indexed_chunks,
        context_chunk_refs(context_chunks),
        chunks_overview,
        sequence_candidate_sections,
        source_file_list or [],
        memory_prompt_tsv(memory_for_prompt, max_rows=400),
        component_memory_prompt_tsv(
            memory_for_prompt,
            max_rows=80,
            source_text_key=source_text_key,
            sequence_candidates=prompt_sequence_candidates,
        ),
        str(chunks_json_path) if chunks_json_path else None,
    )
    has_codex_response = os.environ.get("CDNA_TEST_CODEX_RESPONSE") is not None
    has_codex_key = any(key in os.environ for key in ["CODEX_API_KEY", "OPENAI_API_KEY"])
    base_trace = {
        "prompt_format": "codex_link_prompt_v2_indexed_chunks",
        "prompt": prompt,
        "chunk_index_count": len(indexed_chunks),
        "chunk_manifest_count": len(chunks_overview),
        "total_sequence_candidate_count": len(sequence_candidates),
        "prompt_sequence_candidate_count": len(prompt_sequence_candidates),
        "total_name_mention_count": len(name_mentions),
        "prompt_name_mention_count": len(prompt_name_mentions),
        "prompt_char_count": len(prompt),
        "context_chunks": context_chunks,
        "context_blocks": context_chunks,
        "context_chunk_count": len(context_chunks),
        "sequence_candidate_sections": sequence_candidate_sections,
        "chunks_json_path": str(chunks_json_path) if chunks_json_path else None,
        "source_file_paths": source_file_list or [],
        "codex_sandbox_mode": "read-only",
        "codex_read_only_directories": sorted(
            {
                str(Path(path).expanduser().resolve().parent)
                for path in source_file_list or []
            }
            | ({str(chunks_json_path.expanduser().resolve().parent)} if chunks_json_path is not None else set())
        ),
        "memory": memory_trace(memory_for_prompt),
        "used_test_response": has_codex_response,
        "credentials_present": has_codex_key,
    }
    if has_codex_response or has_codex_key:
        try:
            codex_dirs = {Path(path).expanduser().resolve().parent for path in source_file_list or []}
            if chunks_json_path is not None:
                codex_dirs.add(chunks_json_path.expanduser().resolve().parent)
            raw = complete_with_codex(
                prompt,
                model=os.environ.get("CDNA_CODEX_MODEL"),
                reasoning_effort=os.environ.get("CDNA_CODEX_REASONING_EFFORT"),
                sandbox_mode="read-only",
                additional_directories=codex_dirs,
            )
            parsed = extract_json_object(raw) or {}
            protocol_brief = sanitize_protocol_brief(parsed.get("protocol_brief"))
            augmented_candidates = append_codex_candidates(sequence_candidates, parsed, chunks)
            links = sanitize_links(parsed.get("links"), augmented_candidates, fallback, memory_for_prompt, source_text_key)
            return links, {
                **base_trace,
                "status": "codex_linked",
                "codex_call_count": 1,
                "protocol_brief": protocol_brief,
                "links": links,
                "raw": raw,
            }, augmented_candidates
        except Exception as exc:
            return fallback, {
                **base_trace,
                "status": "fallback_codex_error",
                "error": str(exc),
                "protocol_brief": sanitize_protocol_brief(None),
                "links": fallback,
                "raw": "",
            }, sequence_candidates

    return fallback, {
        **base_trace,
        "status": "skipped_no_coding_agent_credentials",
        "codex_call_count": 0,
        "protocol_brief": sanitize_protocol_brief(None),
        "links": fallback,
        "raw": "",
    }, sequence_candidates


def verify_links(links: list[dict[str, Any]], sequence_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid_candidate_ids = {str(candidate["candidate_id"]) for candidate in sequence_candidates}
    findings: list[dict[str, Any]] = []
    for index, link in enumerate(links, start=1):
        candidate_id = link.get("candidate_id")
        if candidate_id is not None and str(candidate_id) not in valid_candidate_ids:
            findings.append(
                {
                    "link_index": index,
                    "severity": "error",
                    "message": "link references a sequence candidate ID that was not produced by deterministic scanning",
                    "candidate_id": candidate_id,
                }
            )
        for component in link.get("component_candidate_ids") or []:
            if not isinstance(component, dict):
                continue
            component_id = component.get("candidate_id")
            if component_id is not None and str(component_id) not in valid_candidate_ids:
                findings.append(
                    {
                        "link_index": index,
                        "severity": "error",
                        "message": "link references a component candidate ID that was not produced by deterministic scanning",
                        "candidate_id": component_id,
                    }
                )
        if link.get("sequence"):
            findings.append(
                {
                    "link_index": index,
                    "severity": "error",
                    "message": "linker returned a raw sequence string; only candidate_id references are allowed",
                }
            )
    return findings


def _memory_match_for_sequence(sequence: str | None, direction: str, memory: dict[str, Any]) -> dict[str, Any] | None:
    if not sequence:
        return None
    lookup = memory_by_semantic_sequence(memory)
    key = semantic_sequence_key(sequence)
    for candidate_direction in [direction, "unknown"]:
        match = lookup.get((candidate_direction, key))
        if match:
            return match
    if direction == "unknown":
        for candidate_direction in ["5_to_3", "3_to_5"]:
            match = lookup.get((candidate_direction, key))
            if match:
                return match
    return None


def _memory_item_name_keys(item: dict[str, Any]) -> set[str]:
    keys = {display_name_key(str(item.get("name") or ""))}
    keys.update(display_name_key(str(alias)) for alias in item.get("aliases") or [])
    return {key for key in keys if key}


def _memory_item_name_support(item: dict[str, Any], memory: dict[str, Any]) -> int:
    target_keys = _memory_item_name_keys(item)
    if not target_keys:
        return 0
    support = 0
    protocols: set[str] = set()
    for other in memory.get("oligo_nodes") or []:
        if not isinstance(other, dict):
            continue
        if not target_keys & _memory_item_name_keys(other):
            continue
        protocols.update(_memory_protocol_ids(other))
        try:
            support += max(1, int(other.get("protocol_count") or 0))
        except (TypeError, ValueError):
            support += 1
    return max(support, len(protocols))


def _memory_item_is_common_canonical(item: dict[str, Any], support: int) -> bool:
    if support >= 2:
        return True
    text = " ".join([str(item.get("name") or ""), " ".join(str(alias) for alias in item.get("aliases") or [])])
    return bool(
        re.search(
            r"\b("
            r"beads-tso|ispcr|template switching|tso|poly[- ]?dt|illumina|nextera|truseq|"
            r"read\s*[12]|p[57]|i[57]"
            r")\b",
            text,
            flags=re.I,
        )
    )


def _direction_matches_memory(direction: str, item_direction: str) -> bool:
    if direction == "unknown" or item_direction == "unknown":
        return True
    return direction == item_direction


def _memory_similarity_match_for_sequence(
    sequence: str | None,
    direction: str,
    memory: dict[str, Any],
    source_name: str = "",
    *,
    threshold: float = 0.92,
) -> dict[str, Any] | None:
    if not sequence:
        return None
    query_key = semantic_sequence_key(sequence, source_name)
    if not query_key:
        return None
    best: tuple[float, int, int, int, dict[str, Any]] | None = None
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict) or not item.get("sequence") or not item.get("allowed_for_memory_completion", True):
            continue
        item_direction = str(item.get("direction") or "unknown")
        if not _direction_matches_memory(direction, item_direction):
            continue
        item_name = str(item.get("name") or "")
        item_key = semantic_sequence_key(str(item.get("sequence") or ""), item_name)
        if not item_key:
            continue
        similarity = sequence_similarity(query_key, item_key)
        if similarity < threshold:
            continue
        support = _memory_item_name_support(item, memory)
        if not _memory_item_is_common_canonical(item, support):
            continue
        direction_score = 1 if direction != "unknown" and item_direction == direction else 0
        score = (similarity, support, direction_score, len(item_key), item)
        if best is None or score[:4] > best[:4]:
            best = score
    return best[4] if best else None


def _memory_item_id(item: dict[str, Any]) -> str:
    return str(item.get("oligo_id") or item.get("memory_id") or "")


def _memory_item_role(item: dict[str, Any]) -> str:
    role = str(item.get("role") or infer_role(str(item.get("name") or "")))
    return role if role in set(ROLE_TERMS) | {"unknown"} else "unknown"


def _component_from_candidate(candidate: dict[str, Any], role: str) -> OligoComponent:
    sequence, _components = semantic_sequence_and_components(
        str(candidate.get("normalized_sequence") or ""),
        str(candidate.get("name_hint") or ""),
        str(candidate.get("nearby_text") or ""),
    )
    return OligoComponent(order=0, name=role, sequence=sequence, role=role)


def _candidate_component_role(candidate: dict[str, Any]) -> str:
    direction = str(candidate.get("direction") or "unknown")
    if direction == "5_to_3":
        return "forward_strand"
    if direction == "3_to_5":
        return "reverse_strand"
    return "strand"


def _candidate_evidence(candidate: dict[str, Any] | None) -> list[Evidence]:
    if not candidate:
        return []
    evidence = candidate.get("evidence")
    return [Evidence.model_validate(evidence)] if isinstance(evidence, dict) else []


def _double_stranded_name(name: str, components: list[OligoComponent], memory: dict[str, Any]) -> tuple[str, list[str]]:
    return name, []


def _index_placeholder_for_component_join(name: str, notes: str | None) -> str | None:
    text = " ".join([name, notes or ""]).lower()
    if re.search(r"\btn5\b.*\b8\s*[- ]?\s*bp\b|\b8\s*[- ]?\s*bp\b.*\btn5\b", text):
        return "[8-bp Tn5 index]"
    if re.search(r"\bi5\b", text):
        return "[i5]"
    if re.search(r"\bi7\b", text):
        return "[i7]"
    return None


def _sequence_components_for_join(
    name: str,
    components: list[OligoComponent],
    notes: str | None,
) -> tuple[str | None, list[OligoComponent]]:
    if len(components) < 2:
        return None, components
    forward_components = [
        component
        for component in components
        if re.search(r"\b(?:forward|top)[_ -]?strand(?:\b|_)", str(component.role or component.name), flags=re.I)
    ]
    reverse_components = [
        component
        for component in components
        if re.search(r"\b(?:reverse|bottom)[_ -]?strand(?:\b|_)", str(component.role or component.name), flags=re.I)
    ]
    if forward_components and reverse_components:
        join_components = forward_components
    elif not reverse_components:
        join_components = components
    else:
        return None, components
    if len(join_components) < 2:
        return None, components
    fixed_sequences = [component.sequence for component in join_components]
    if any(not sequence for sequence in fixed_sequences):
        return None, components
    placeholder = _index_placeholder_for_component_join(name, notes)
    parts: list[str] = [str(join_components[0].sequence)]
    joined_components: list[OligoComponent] = [
        OligoComponent(order=1, name=join_components[0].name, sequence=join_components[0].sequence, role=join_components[0].role)
    ]
    output_order = 2
    if placeholder:
        parts.append(placeholder)
        joined_components.append(
            OligoComponent(
                order=output_order,
                name=placeholder,
                sequence=placeholder,
                role="sample_index",
            )
        )
        output_order += 1
    for component in join_components[1:]:
        parts.append(str(component.sequence))
        joined_components.append(
            OligoComponent(order=output_order, name=component.name, sequence=component.sequence, role=component.role)
        )
        output_order += 1
    return "".join(parts), joined_components


def _canonical_component_join_name(name: str, sequence: str | None, components: list[OligoComponent], notes: str | None) -> str:
    text = " ".join([name, notes or "", sequence or "", " ".join(component.name for component in components)]).lower()
    compact_text = re.sub(r"[^a-z0-9]+", "", text)
    if "a14" in text and "lna" in text and "me" in text:
        return "A14_ME_LNA (Nextera_R1_A14 + U-ME)"
    if "sbs" in text and "ume" in compact_text and "tn5" in text:
        return "SBS12_18_UME_sci indexed Tn5 adapter"
    if re.search(r"\bi5\b", text) and "pcr" in text:
        return "PCR_i5_primer"
    if re.search(r"\bi7\b", text) and "pcr" in text:
        return "TruSeq i7 PCR primer"
    return name


def _link_components(
    link: dict[str, Any],
    candidate: dict[str, Any] | None,
    candidate_by_id: dict[str, dict[str, Any]],
    candidates_by_block: dict[str, list[dict[str, Any]]],
) -> list[OligoComponent]:
    components: list[OligoComponent] = []
    raw_component_ids = link.get("component_candidate_ids") or []
    if isinstance(raw_component_ids, list):
        for raw_component in raw_component_ids:
            if not isinstance(raw_component, dict):
                continue
            component_candidate = candidate_by_id.get(str(raw_component.get("candidate_id") or ""))
            if not component_candidate:
                continue
            role = str(raw_component.get("role") or _candidate_component_role(component_candidate))
            components.append(_component_from_candidate(component_candidate, role))
    if components:
        for index, component in enumerate(components, start=1):
            component.order = index
        return components

    if not candidate:
        return []
    block_candidates = candidates_by_block.get(str(candidate.get("block_id") or ""), [])
    if len(block_candidates) < 2:
        return []
    directions = {str(item.get("direction") or "unknown") for item in block_candidates}
    if not {"5_to_3", "3_to_5"}.issubset(directions):
        return []
    link_role = str(link.get("role") or infer_role(str(link.get("name") or "")))
    if link_role != "adapter":
        return []
    for block_candidate in block_candidates:
        components.append(_component_from_candidate(block_candidate, _candidate_component_role(block_candidate)))
    for index, component in enumerate(components, start=1):
        component.order = index
    return components


def _protocol_brief_text_key(protocol_brief: dict[str, Any] | None) -> str:
    if not isinstance(protocol_brief, dict):
        return ""
    parts: list[str] = []
    for key in ["summary"]:
        value = protocol_brief.get(key)
        if isinstance(value, str):
            parts.append(value)
    steps = protocol_brief.get("major_steps")
    if isinstance(steps, list):
        parts.extend(str(step) for step in steps if str(step))
    return display_name_key(" ".join(parts))


def _brief_routes_memory_item(item: dict[str, Any], brief_text_key: str) -> bool:
    if not brief_text_key:
        return True
    brief_tokens = set(brief_text_key.split())
    generic = {
        "adapter",
        "adaptor",
        "barcode",
        "barcoded",
        "cell",
        "dna",
        "gene",
        "index",
        "indexed",
        "kit",
        "library",
        "oligo",
        "pcr",
        "primer",
        "read",
        "rna",
        "sample",
        "seq",
        "sequencing",
        "single",
        "small",
    }
    item_text = " ".join(
        [
            str(item.get("name") or ""),
            " ".join(str(alias) for alias in item.get("aliases") or []),
            " ".join(str(value) for value in item.get("source_protocol_ids") or []),
            " ".join(str(value) for value in item.get("source_protocol_names") or []),
        ]
    )
    item_key = display_name_key(item_text)
    if not item_key:
        return False
    phrases = {
        "10x chromium",
        "cel seq",
        "nextera",
        "sci rna",
        "smart seq",
        "truseq",
    }
    if any(phrase in item_key and phrase in brief_text_key for phrase in phrases):
        return True
    for token in item_key.split():
        if token in generic:
            continue
        if len(token) >= 3 and token in brief_tokens:
            return True
        if token in {"10x", "p5", "p7", "i5", "i7"} and re.search(rf"\b{re.escape(token)}\b", brief_text_key):
            return True
    return False


def _is_cel_seq_context(protocol_id: str, protocol_name: str, brief_text_key: str, source_text_key: str) -> bool:
    context_key = display_name_key(" ".join([protocol_id, protocol_name, brief_text_key, source_text_key]))
    return bool(re.search(r"\bcel\b", context_key))


def _supported_memory_oligos(
    protocol_id: str,
    protocol_name: str,
    source_files: list[str],
    existing_oligos: list[Oligo],
    sequence_candidates: list[dict[str, Any]],
    memory: dict[str, Any],
    protocol_brief: dict[str, Any] | None = None,
) -> list[Oligo]:
    reviewed_completion_re = re.compile(
        r"\b("
        r"beads-tso"
        r"|barcoded rt primer variant [12]"
        r"|cdna primer mix reverse"
        r"|illumina p5 adapter"
        r"|illumina p7 adapter"
        r"|illumina truseq read [12] primer"
        r"|illumina ra[35] adapter"
        r"|illumina rp1 primer"
        r"|illumina rpi primers"
        r"|illumina rtp primer"
        r"|illumina truseq small rna read [12] primer"
        r"|library pcr primer [12] \(pn-"
        r"|index read primer"
        r"|randomhexrt primer"
        r"|t7 promoter"
        r"|truseq sample index sequencing primer forward"
        r")\b",
        flags=re.I,
    )
    existing_names = {display_name_key(oligo.name) for oligo in existing_oligos}
    existing_sequences = {sequence_key(oligo.sequence) for oligo in existing_oligos if oligo.sequence}
    memory_items_by_id = _memory_item_by_id(memory)
    source_text_parts: list[str] = []
    for candidate in sequence_candidates:
        source_text_parts.extend(
            str(candidate.get(key) or "")
            for key in ["name_hint", "nearby_text", "quote", "raw_sequence", "normalized_sequence"]
        )
        evidence = candidate.get("evidence")
        if isinstance(evidence, dict):
            source_text_parts.append(str(evidence.get("quote") or ""))
    for oligo in existing_oligos:
        source_text_parts.extend([oligo.name, " ".join(oligo.aliases), oligo.notes or ""])
        for evidence in oligo.evidence:
            source_text_parts.append(evidence.quote or "")
    source_text_key = display_name_key(" ".join(source_text_parts))
    brief_text_key = _protocol_brief_text_key(protocol_brief)
    supported_source_protocol_ids: set[str] = set()
    for oligo in existing_oligos:
        if not oligo.memory_id:
            continue
        memory_item = memory_items_by_id.get(oligo.memory_id)
        if memory_item:
            supported_source_protocol_ids.update(_memory_protocol_ids(memory_item))
    candidate_records: list[tuple[str, str, dict[str, Any]]] = []
    for candidate in sequence_candidates:
        candidate_key = support_sequence_key(
            str(candidate.get("normalized_sequence") or ""),
            str(candidate.get("name_hint") or ""),
            str(candidate.get("nearby_text") or ""),
        )
        if candidate_key:
            candidate_records.append(
                (
                    candidate_key,
                    support_sequence_without_variable_placeholders(
                        str(candidate.get("normalized_sequence") or ""),
                        str(candidate.get("name_hint") or ""),
                        str(candidate.get("nearby_text") or ""),
                    ),
                    candidate,
                )
            )
    existing_records: list[tuple[str, str, Oligo]] = []
    for oligo in existing_oligos:
        oligo_key = support_sequence_key(oligo.sequence, oligo.name)
        if oligo_key:
            existing_records.append((oligo_key, support_sequence_without_variable_placeholders(oligo.sequence, oligo.name), oligo))

    supported: list[Oligo] = []
    seen: set[tuple[str, str]] = set()
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict) or not item.get("sequence"):
            continue
        name = str(item.get("name") or "")
        if not reviewed_completion_re.search(name):
            continue
        name_key = display_name_key(name)
        sequence, components = semantic_sequence_and_components(str(item.get("sequence") or ""), name)
        seq_key = sequence_key(sequence)
        if not name_key or not seq_key or name_key in existing_names or seq_key in existing_sequences:
            continue
        support_key = support_sequence_key(sequence, name)
        collapsed = support_sequence_without_variable_placeholders(sequence, name)
        support_candidate: dict[str, Any] | None = None
        support_oligo: Oligo | None = None
        support_strength: str | None = None
        has_same_memory_protocol = bool(_memory_protocol_ids(item) & supported_source_protocol_ids)
        route_allowed = _brief_routes_memory_item(item, brief_text_key)
        name_terms = [
            display_name_key(name),
            *(display_name_key(str(alias)) for alias in item.get("aliases") or []),
        ]
        name_mentioned = any(term and len(term) >= 4 and term in source_text_key for term in name_terms)
        if not name_mentioned and display_name_key(name) == display_name_key("cDNA Primer Mix reverse"):
            name_mentioned = "cdna reverse" in source_text_key
        if (
            re.search(r"\bbarcoded rt primer variant [12]\b", name, flags=re.I)
            and not name_mentioned
            and not _is_cel_seq_context(protocol_id, protocol_name, brief_text_key, source_text_key)
        ):
            continue
        for candidate_key, candidate_collapsed, candidate in candidate_records:
            exact = candidate_key == support_key
            contained = len(support_key) >= 20 and support_key in candidate_key
            same_backbone = collapsed and candidate_collapsed and collapsed == candidate_collapsed and len(collapsed) >= 18
            anchor_supported = len(support_key) >= 20 and (support_key[:20] in candidate_key or support_key[-20:] in candidate_key)
            if exact or contained or same_backbone or anchor_supported:
                support_candidate = candidate
                if exact:
                    support_strength = "exact"
                elif contained:
                    support_strength = "contained"
                elif same_backbone:
                    support_strength = "same_backbone"
                else:
                    support_strength = "anchor"
                break
        if not support_candidate:
            for existing_key, existing_collapsed, existing_oligo in existing_records:
                exact = existing_key == support_key
                contained = len(support_key) >= 20 and support_key in existing_key
                same_backbone = collapsed and existing_collapsed and collapsed == existing_collapsed and len(collapsed) >= 18
                anchor_supported = len(support_key) >= 20 and (support_key[:20] in existing_key or support_key[-20:] in existing_key)
                if exact or contained or same_backbone or anchor_supported or has_same_memory_protocol:
                    support_oligo = existing_oligo
                    if exact:
                        support_strength = "exact"
                    elif contained:
                        support_strength = "contained"
                    elif same_backbone:
                        support_strength = "same_backbone"
                    elif anchor_supported:
                        support_strength = "anchor"
                    else:
                        support_strength = "same_memory_protocol"
                    break
        if not support_candidate and not support_oligo:
            continue
        if support_strength != "exact" and not name_mentioned:
            same_family_allowed = has_same_memory_protocol and re.search(
                r"\b(?:barcoded rt primer variant [12]|t7 promoter)\b",
                name,
                flags=re.I,
            ) and route_allowed
            if not same_family_allowed:
                continue
        role = _memory_item_role(item)
        if role == "unknown":
            continue
        key = (name_key, seq_key)
        if key in seen:
            continue
        seen.add(key)
        existing_names.add(name_key)
        existing_sequences.add(seq_key)
        kind = str(item.get("kind") or infer_kind(name, sequence, len(components)))
        if kind not in {"single", "assembled", "double_stranded", "hairpin"}:
            kind = infer_kind(name, sequence, len(components))
        supported.append(
            Oligo(
                oligo_id=f"oligo_{slug(name)}",
                protocol_id=protocol_id,
                protocol_name=protocol_name,
                name=name,
                aliases=[str(alias) for alias in item.get("aliases") or [] if str(alias)],
                role=role,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                sequence=sequence,
                direction=str(item.get("direction") or "unknown") if str(item.get("direction") or "unknown") in {"5_to_3", "3_to_5", "unknown"} else "unknown",  # type: ignore[arg-type]
                components=components,
                sequence_source="memory_completed",
                memory_id=_memory_item_id(item),
                evidence=_candidate_evidence(support_candidate) if support_candidate else (support_oligo.evidence if support_oligo else []),
                notes=annotate_assay_note(
                    "Added from memory because the sequence/backbone is supported by protocol evidence.",
                    "gex",
                ),
            )
        )
        if len(supported) >= 24:
            break
    return supported


VARIABLE_COMPONENT_ROLES = {"cell_barcode", "umi", "sample_index", "barcode", "rt_barcode", "polyT", "variable"}


def _component_edge_is_emit_candidate(edge: dict[str, Any]) -> bool:
    role = str(edge.get("component_role") or "").strip().lower()
    sequence = str(edge.get("component_sequence") or "").strip()
    name = str(edge.get("component_name") or "").strip()
    if not name or not sequence or role in VARIABLE_COMPONENT_ROLES:
        return False
    if sequence.lower() == "polyt" or "[" in sequence or "]" in sequence:
        return False
    return bool(re.search(r"[ACGTU]{8,}", sequence, flags=re.I))


def _component_edge_parent_matches_oligo(edge: dict[str, Any], oligo: Oligo) -> bool:
    parent_name_key = display_name_key(str(edge.get("parent_oligo_name") or ""))
    if parent_name_key and parent_name_key == display_name_key(oligo.name):
        return True
    parent_sequence = str(edge.get("parent_sequence") or "")
    if not parent_sequence or not oligo.sequence:
        return False
    parent_key = support_sequence_key(parent_sequence, str(edge.get("parent_oligo_name") or ""))
    oligo_key = support_sequence_key(oligo.sequence, oligo.name)
    parent_collapsed = support_sequence_without_variable_placeholders(parent_sequence, str(edge.get("parent_oligo_name") or ""))
    oligo_collapsed = support_sequence_without_variable_placeholders(oligo.sequence, oligo.name)
    return bool(
        parent_key
        and oligo_key
        and (
            parent_key == oligo_key
            or parent_key in oligo_key
            or (parent_collapsed and parent_collapsed == oligo_collapsed and len(parent_collapsed) >= 16)
        )
    )


def _component_edge_parent_matches_for_completion(edge: dict[str, Any], oligo: Oligo) -> bool:
    parent_name_key = display_name_key(str(edge.get("parent_oligo_name") or ""))
    if parent_name_key and parent_name_key == display_name_key(oligo.name):
        return True
    parent_sequence = str(edge.get("parent_sequence") or "")
    if not parent_sequence or not oligo.sequence:
        return False
    parent_key = support_sequence_key(parent_sequence, str(edge.get("parent_oligo_name") or ""))
    oligo_key = support_sequence_key(oligo.sequence, oligo.name)
    parent_collapsed = support_sequence_without_variable_placeholders(parent_sequence, str(edge.get("parent_oligo_name") or ""))
    oligo_collapsed = support_sequence_without_variable_placeholders(oligo.sequence, oligo.name)
    return bool(
        parent_key
        and oligo_key
        and (
            parent_key == oligo_key
            or parent_key in oligo_key
            or (parent_collapsed and parent_collapsed == oligo_collapsed and len(parent_collapsed) >= 16)
        )
    )


def _component_edge_sequence_is_in_parent(edge: dict[str, Any], oligo: Oligo) -> bool:
    component_sequence = str(edge.get("component_sequence") or "")
    component_key = sequence_key(component_sequence)
    if not component_key:
        return False
    parent_values = [
        str(oligo.sequence or ""),
        str(edge.get("parent_sequence") or ""),
        support_sequence_key(str(oligo.sequence or ""), oligo.name),
        support_sequence_key(str(edge.get("parent_sequence") or ""), str(edge.get("parent_oligo_name") or "")),
    ]
    return any(component_key in sequence_key(value) for value in parent_values if value)


def _oligo_can_trigger_component_memory(oligo: Oligo) -> bool:
    if oligo.kind != "assembled" or not oligo.sequence:
        return False
    if oligo.sequence_source == "memory_completed":
        return False
    return not is_construct_or_product_name(oligo.name)


def _component_edge_context_allowed(edge: dict[str, Any], source_text_key: str) -> bool:
    edge_text = display_name_key(
        " ".join(
            [
                str(edge.get("parent_oligo_name") or ""),
                str(edge.get("component_name") or ""),
            ]
        )
    )
    gated_terms = {
        "acrydite": ["acrydite"],
        "atac": ["atac"],
        "bd": ["bd", "rhapsody"],
        "feature": ["feature", "fb", "antibody", "crispr"],
        "hydrop": ["hydrop"],
        "mgi": ["mgi"],
        "pip": ["pip"],
        "rhapsody": ["rhapsody"],
        "solexa": ["solexa"],
        "strt": ["strt"],
    }
    for term, allowed_context_terms in gated_terms.items():
        if term in edge_text and not any(context_term in source_text_key for context_term in allowed_context_terms):
            return False
    return True


def _component_edge_child_is_assembled(edge: dict[str, Any], all_edges: list[dict[str, Any]]) -> bool:
    child_name_key = display_name_key(str(edge.get("component_name") or ""))
    child_sequence = str(edge.get("component_sequence") or "")
    child_sequence_key = sequence_key(child_sequence)
    if not child_name_key or not child_sequence_key:
        return False
    child_parts: list[tuple[str, str]] = []
    for candidate in all_edges:
        if display_name_key(str(candidate.get("parent_oligo_name") or "")) != child_name_key:
            continue
        if not _component_edge_is_emit_candidate(candidate):
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


def _component_edge_preference(edge: dict[str, Any]) -> tuple[int, int, int, int]:
    sequence_length = len(sequence_key(str(edge.get("component_sequence") or "")))
    name_key = display_name_key(str(edge.get("component_name") or ""))
    canonical_bonus = 0
    if any(term in name_key for term in ["illumina", "truseq", "nextera", "tn5"]):
        canonical_bonus += 4
    if any(term in name_key for term in ["atac", "solexa", "hydrop", "mgi", "acrydite", "feature"]):
        canonical_bonus -= 4
    try:
        protocol_count = int(edge.get("protocol_count") or 0)
    except (TypeError, ValueError):
        protocol_count = 0
    return sequence_length, canonical_bonus, protocol_count, -len(name_key)


def _filter_overlapping_component_edges(
    edges: list[dict[str, Any]],
    source_text_key: str,
    all_edges: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    component_universe = all_edges or edges
    candidates = [
        edge
        for edge in edges
        if _component_edge_is_emit_candidate(edge)
        and _component_edge_context_allowed(edge, source_text_key)
        and not _component_edge_child_is_assembled(edge, component_universe)
    ]
    candidates.sort(key=_component_edge_preference, reverse=True)
    kept: list[dict[str, Any]] = []
    kept_sequences: list[str] = []
    for edge in candidates:
        sequence = sequence_key(str(edge.get("component_sequence") or ""))
        if not sequence:
            continue
        if any(sequence == kept_sequence or sequence in kept_sequence for kept_sequence in kept_sequences):
            continue
        kept.append(edge)
        kept_sequences.append(sequence)
    kept.sort(
        key=lambda edge: (
            int(edge.get("component_order") or 0),
            display_name_key(str(edge.get("component_name") or "")),
        )
    )
    return kept


def _component_from_edge(edge: dict[str, Any], order: int) -> OligoComponent:
    return OligoComponent(
        order=order,
        name=str(edge.get("component_name") or ""),
        sequence=str(edge.get("component_sequence") or ""),
        role=str(edge.get("component_role") or infer_role(str(edge.get("component_name") or ""))),
    )


def _component_sequence_is_already_represented(sequence: str, components: list[OligoComponent]) -> bool:
    seq_key = sequence_key(sequence)
    if not seq_key:
        return True
    for component in components:
        existing_key = sequence_key(component.sequence)
        if not existing_key:
            continue
        if seq_key == existing_key or seq_key in existing_key or existing_key in seq_key:
            return True
    return False


def _component_sequence_is_strict_subsequence(sequence: str, components: list[OligoComponent]) -> bool:
    seq_key = sequence_key(sequence)
    if not seq_key:
        return True
    for component in components:
        existing_key = sequence_key(component.sequence)
        if existing_key and seq_key != existing_key and seq_key in existing_key:
            return True
    return False


def _complete_assembled_components_from_memory(oligos: list[Oligo], memory: dict[str, Any], source_text_key: str) -> None:
    component_edges = [edge for edge in memory.get("assembled_component_edges") or [] if isinstance(edge, dict)]
    if not component_edges:
        return
    for oligo in oligos:
        if not _oligo_can_trigger_component_memory(oligo):
            continue
        components = list(oligo.components)
        component_keys = {
            (display_name_key(component.name), sequence_key(component.sequence))
            for component in components
            if component.name or component.sequence
        }
        matching_edges = [
            edge
            for edge in component_edges
            if _component_edge_parent_matches_for_completion(edge, oligo)
            and _component_edge_sequence_is_in_parent(edge, oligo)
        ]
        for edge in _filter_overlapping_component_edges(matching_edges, source_text_key, component_edges):
            name = str(edge.get("component_name") or "")
            sequence = str(edge.get("component_sequence") or "")
            key = (display_name_key(name), sequence_key(sequence))
            if not key[0] or not key[1] or key in component_keys:
                continue
            if _component_sequence_is_already_represented(sequence, components):
                continue
            component_keys.add(key)
            components.append(_component_from_edge(edge, int(edge.get("component_order") or len(components) + 1)))
        if len(components) == len(oligo.components):
            continue
        components.sort(key=lambda component: (component.order or 0, display_name_key(component.name)))
        for index, component in enumerate(components, start=1):
            component.order = index
        oligo.components = components
        if oligo.kind == "single":
            oligo.kind = "assembled"


def _strip_output_aliases(oligos: list[Oligo]) -> None:
    for oligo in oligos:
        oligo.aliases = []


def _source_text_key_for_support(existing_oligos: list[Oligo], sequence_candidates: list[dict[str, Any]], protocol_brief: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for candidate in sequence_candidates:
        parts.extend(
            str(candidate.get(key) or "")
            for key in ["name_hint", "nearby_text", "quote", "raw_sequence", "normalized_sequence"]
        )
        evidence = candidate.get("evidence")
        if isinstance(evidence, dict):
            parts.append(str(evidence.get("quote") or ""))
    for oligo in existing_oligos:
        parts.extend([oligo.name, " ".join(oligo.aliases), oligo.notes or ""])
        for evidence in oligo.evidence:
            parts.append(evidence.quote or "")
    if isinstance(protocol_brief, dict):
        parts.append(str(protocol_brief.get("summary") or ""))
        steps = protocol_brief.get("major_steps")
        if isinstance(steps, list):
            parts.extend(str(step) for step in steps)
    return display_name_key(" ".join(parts))


def _component_edge_context_supported(edge: dict[str, Any], source_text_key: str, existing_names: set[str]) -> bool:
    if not source_text_key:
        return False
    component_name_key = display_name_key(str(edge.get("component_name") or ""))
    if component_name_key in existing_names:
        return False
    if not _component_edge_context_allowed(edge, source_text_key):
        return False
    has_nextera_context = "nextera" in source_text_key and ("tn5" in source_text_key or "tagment" in source_text_key)
    if has_nextera_context and any(term in component_name_key for term in ["nextera n s5xx", "nextera n7xx", "nextera tn5 binding site"]):
        return True
    return False


def _supported_component_memory_oligos(
    protocol_id: str,
    protocol_name: str,
    existing_oligos: list[Oligo],
    sequence_candidates: list[dict[str, Any]],
    memory: dict[str, Any],
    protocol_brief: dict[str, Any] | None = None,
) -> list[Oligo]:
    component_edges = [edge for edge in memory.get("assembled_component_edges") or [] if isinstance(edge, dict)]
    if not component_edges:
        return []
    source_text_key = _source_text_key_for_support(existing_oligos, sequence_candidates, protocol_brief)
    existing_names = {display_name_key(oligo.name) for oligo in existing_oligos}
    existing_sequences = {sequence_key(oligo.sequence) for oligo in existing_oligos if oligo.sequence}
    supported: list[Oligo] = []
    seen: set[tuple[str, str]] = set()

    def add_from_edge(edge: dict[str, Any], evidence_oligo: Oligo | None, reason: str) -> None:
        if not _component_edge_is_emit_candidate(edge):
            return
        name = str(edge.get("component_name") or "")
        name_key = display_name_key(name)
        sequence, components = semantic_sequence_and_components(str(edge.get("component_sequence") or ""), name)
        seq_key = sequence_key(sequence)
        if not name_key or not seq_key or name_key in existing_names or seq_key in existing_sequences:
            return
        if evidence_oligo and _component_sequence_is_strict_subsequence(sequence, evidence_oligo.components):
            return
        key = (name_key, seq_key)
        if key in seen:
            return
        role = str(edge.get("component_role") or infer_role(name))
        if role not in set(ROLE_TERMS) | {"unknown"}:
            role = infer_role(name)
        direction = str(edge.get("parent_direction") or "5_to_3")
        if direction not in {"5_to_3", "3_to_5", "unknown"}:
            direction = "5_to_3"
        seen.add(key)
        existing_names.add(name_key)
        existing_sequences.add(seq_key)
        supported.append(
            Oligo(
                oligo_id=f"oligo_{slug(name)}",
                protocol_id=protocol_id,
                protocol_name=protocol_name,
                name=name,
                aliases=[],
                role=role,  # type: ignore[arg-type]
                kind=infer_kind(name, sequence, len(components)),  # type: ignore[arg-type]
                sequence=sequence,
                direction=direction,  # type: ignore[arg-type]
                components=components,
                sequence_source="memory_completed",
                memory_id=str(edge.get("component_memory_id") or ""),
                evidence=evidence_oligo.evidence if evidence_oligo else [],
                notes=annotate_assay_note(
                    f"Added from assembled oligo component memory because {reason}.",
                    "gex",
                ),
            )
        )

    for oligo in existing_oligos:
        if not _oligo_can_trigger_component_memory(oligo):
            continue
        matching_edges = [
            edge
            for edge in component_edges
            if _component_edge_parent_matches_oligo(edge, oligo)
            and _component_edge_sequence_is_in_parent(edge, oligo)
        ]
        for edge in _filter_overlapping_component_edges(matching_edges, source_text_key, component_edges):
            add_from_edge(edge, oligo, f"source-supported parent '{oligo.name}' contains this component")

    for edge in component_edges:
        if not _component_edge_context_supported(edge, source_text_key, existing_names):
            continue
        add_from_edge(edge, existing_oligos[0] if existing_oligos else None, "the protocol context supports the same assembled-component family")

    return supported


def _numbered_series_base(name: str) -> str | None:
    match = re.search(r"^(?P<base>.+?)\s*(?:#|no\.?\s*)\d+\s*$", name.strip(), flags=re.I)
    if not match:
        return None
    base = re.sub(r"\bcel[- ]seq\b", "", match.group("base"), flags=re.I)
    base = re.sub(r"\s+", " ", base).strip(" -")
    return base or None


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        index = 0
        while index < min(len(prefix), len(value)) and prefix[index] == value[index]:
            index += 1
        prefix = prefix[:index]
        if not prefix:
            break
    return prefix


def _common_suffix(values: list[str], max_start: int = 0) -> str:
    if not values:
        return ""
    reversed_suffix = _common_prefix([value[max_start:][::-1] for value in values])
    return reversed_suffix[::-1]


def _matching_memory_pattern(sequence: str, name: str, memory: dict[str, Any]) -> dict[str, Any] | None:
    seq_key = semantic_sequence_key(sequence, name)
    if not seq_key:
        return None
    matches: dict[tuple[str, str], dict[str, Any]] = {}
    for item in memory.get("oligo_nodes") or []:
        if not isinstance(item, dict) or not item.get("sequence") or not item.get("allowed_for_memory_completion", True):
            continue
        item_name = str(item.get("name") or "")
        item_key = semantic_sequence_key(str(item.get("sequence") or ""), item_name)
        if item_key != seq_key:
            continue
        key = (display_name_key(item_name), item_key)
        matches[key] = item
    if matches:
        for item in matches.values():
            if re.search(r"\bvariant\s+\d+\b", str(item.get("name") or ""), flags=re.I):
                return item
        return next(iter(matches.values()))
    return None


def _collapse_barcoded_series(protocol_id: str, protocol_name: str, oligos: list[Oligo], memory: dict[str, Any]) -> list[Oligo]:
    groups: dict[str, list[Oligo]] = {}
    for oligo in oligos:
        if not oligo.sequence:
            continue
        base = _numbered_series_base(oligo.name)
        if not base:
            continue
        groups.setdefault(display_name_key(base), []).append(oligo)

    remove_ids: set[str] = set()
    collapsed: list[Oligo] = []
    for _base_key, members in groups.items():
        if len(members) < 4:
            continue
        sequences = [str(member.sequence) for member in members if member.sequence]
        lengths = {len(sequence) for sequence in sequences}
        if len(lengths) != 1:
            continue
        prefix = _common_prefix(sequences)
        poly_suffix_match = re.search(r"T{8,}V?$", sequences[0])
        if poly_suffix_match and all(sequence.endswith(poly_suffix_match.group(0)) for sequence in sequences):
            suffix = poly_suffix_match.group(0)
        else:
            suffix = _common_suffix(sequences, max_start=len(prefix))
        variable_length = next(iter(lengths)) - len(prefix) - len(suffix)
        if len(prefix) < 12 or len(suffix) < 8 or not (4 <= variable_length <= 20):
            continue
        variable_segments = {sequence[len(prefix) : len(prefix) + variable_length] for sequence in sequences}
        if len(variable_segments) < 2:
            continue
        first = members[0]
        base_name = _numbered_series_base(first.name) or first.name
        role = "cell_barcode" if re.search(r"\bbarcode|barcoded\b", base_name, flags=re.I) else "barcode"
        pattern_sequence = f"{prefix}{'B' * variable_length}{suffix}"
        memory_match = _matching_memory_pattern(pattern_sequence, base_name, memory)
        if memory_match:
            name = str(memory_match.get("name") or base_name)
            pattern_sequence, components = semantic_sequence_and_components(str(memory_match.get("sequence") or pattern_sequence), name)
            memory_id = _memory_item_id(memory_match)
            aliases = [str(alias) for alias in memory_match.get("aliases") or [] if str(alias)]
            direction = str(memory_match.get("direction") or first.direction)
        else:
            name = re.sub(r"\s+", " ", base_name).strip()
            if not re.search(r"\bvariant\b", name, flags=re.I):
                name = f"{name} variant 1"
            components = [_placeholder_component(variable_length, role)]
            direction = first.direction
            memory_id = None
            aliases = []
        if direction not in {"5_to_3", "3_to_5", "unknown"}:
            direction = "unknown"
        for index, component in enumerate(components, start=1):
            component.order = index
        remove_ids.update(member.oligo_id for member in members)
        collapsed.append(
            Oligo(
                oligo_id=f"oligo_{slug(name)}",
                protocol_id=protocol_id,
                protocol_name=protocol_name,
                name=name,
                aliases=aliases + [member.name for member in members[:5]],
                role="primer",
                kind="assembled",
                sequence=pattern_sequence,
                direction=direction,  # type: ignore[arg-type]
                components=components,
                sequence_source="explicit_in_protocol",
                memory_id=memory_id,
                evidence=members[0].evidence,
                notes=annotate_assay_note(
                    f"Collapsed {len(members)} explicit numbered oligos into a consensus pattern with a {variable_length}-bp {role.replace('_', ' ')}.",
                    "gex",
                ),
            )
        )
    if not collapsed:
        return oligos
    retained = [oligo for oligo in oligos if oligo.oligo_id not in remove_ids]
    existing_names = {display_name_key(oligo.name) for oligo in retained}
    for oligo in collapsed:
        if display_name_key(oligo.name) not in existing_names:
            retained.append(oligo)
            existing_names.add(display_name_key(oligo.name))
    return retained


def _prune_shadowed_oligos(oligos: list[Oligo]) -> list[Oligo]:
    component_sequences = {
        sequence_key(component.sequence)
        for oligo in oligos
        if oligo.kind == "double_stranded"
        for component in oligo.components
        if component.sequence
    }
    by_name = {display_name_key(oligo.name): oligo for oligo in oligos}
    cdna_primer_mix = by_name.get(display_name_key("cDNA Primer Mix reverse (PN-220106)"))
    cdna_primer_mix_key = sequence_key(cdna_primer_mix.sequence) if cdna_primer_mix else ""
    pruned: list[Oligo] = []
    for oligo in oligos:
        name_key = display_name_key(oligo.name)
        oligo_seq_key = sequence_key(oligo.sequence)
        if is_construct_or_product_name(oligo.name):
            continue
        if re.search(r"\btruseq adapter (?:forward|reverse)\b", oligo.name, flags=re.I) and oligo_seq_key in component_sequences:
            continue
        if (
            cdna_primer_mix_key
            and name_key in {display_name_key("cDNA reverse primer"), display_name_key("Reverse Primer")}
            and oligo.sequence_source == "explicit_in_protocol"
            and oligo_seq_key.startswith(cdna_primer_mix_key)
        ):
            continue
        pruned.append(oligo)

    def score(oligo: Oligo) -> tuple[int, int]:
        name_key = display_name_key(oligo.name)
        value = 0
        if oligo.sequence_source == "explicit_in_protocol":
            value += 20
        if oligo.memory_id:
            value += 3
        if name_key == display_name_key("Barcoded RT primer"):
            value += 30
        if name_key == display_name_key("PCR P5 primer"):
            value += 30
        if name_key == display_name_key("Nextera N7 index primer"):
            value += 30
        if name_key in {display_name_key("anchored oligo-dT primer"), display_name_key("P5 primer"), display_name_key("P7 primer")}:
            value -= 30
        return value, -len(oligo.name)

    def names_are_related(left: Oligo, right: Oligo) -> bool:
        left_key = display_name_key(left.name)
        right_key = display_name_key(right.name)
        if not left_key or not right_key:
            return False
        if left_key in right_key or right_key in left_key:
            return True
        weak_tokens = {"pcr", "rt", "read", "index", "custom", "designed"}
        left_tokens = {token for token in left_key.split() if token not in weak_tokens}
        right_tokens = {token for token in right_key.split() if token not in weak_tokens}
        return len(left_tokens & right_tokens) >= 2

    prefix_shadowed_ids: set[str] = set()
    sequenced_pruned = [oligo for oligo in pruned if sequence_key(oligo.sequence)]
    for shorter in sequenced_pruned:
        short_key = sequence_key(shorter.sequence)
        if len(short_key) < 16:
            continue
        for longer in sequenced_pruned:
            if shorter.oligo_id == longer.oligo_id:
                continue
            long_key = sequence_key(longer.sequence)
            if len(long_key) < len(short_key) + 8:
                continue
            if not (long_key.startswith(short_key) or short_key in long_key):
                continue
            if names_are_related(shorter, longer):
                prefix_shadowed_ids.add(shorter.oligo_id)
                break
    if prefix_shadowed_ids:
        pruned = [oligo for oligo in pruned if oligo.oligo_id not in prefix_shadowed_ids]

    by_sequence: dict[str, list[Oligo]] = {}
    for oligo in pruned:
        seq_key = sequence_key(oligo.sequence)
        if seq_key:
            by_sequence.setdefault(seq_key, []).append(oligo)
    keep_ids: set[str] = set()
    drop_ids: set[str] = set()
    for seq_key, members in by_sequence.items():
        if len(members) < 2:
            continue
        best = max(members, key=score)
        keep_ids.add(best.oligo_id)
        for member in members:
            if member.oligo_id != best.oligo_id:
                drop_ids.add(member.oligo_id)
    if not drop_ids:
        return pruned
    return [oligo for oligo in pruned if oligo.oligo_id not in drop_ids or oligo.oligo_id in keep_ids]


def resolve_oligos(
    protocol_id: str,
    protocol_name: str,
    split: str,
    source_files: list[str],
    links: list[dict[str, Any]],
    sequence_candidates: list[dict[str, Any]],
    memory: dict[str, Any],
    protocol_brief: dict[str, Any] | None = None,
) -> ProtocolOligoSet:
    candidate_by_id = {str(candidate["candidate_id"]): candidate for candidate in sequence_candidates}
    candidates_by_block: dict[str, list[dict[str, Any]]] = {}
    candidate_sequence_counts: dict[str, int] = {}
    for candidate in sequence_candidates:
        candidates_by_block.setdefault(str(candidate.get("block_id") or ""), []).append(candidate)
        candidate_sequence_counts[sequence_key(candidate.get("normalized_sequence"))] = (
            candidate_sequence_counts.get(sequence_key(candidate.get("normalized_sequence")), 0) + 1
        )
    memory_lookup = memory_by_name(memory)
    oligos: list[Oligo] = []
    seen: set[tuple[str, str]] = set()
    sequenced_name_keys: set[str] = set()

    for index, link in enumerate(links, start=1):
        name = str(link.get("name") or f"Oligo candidate {index}").strip()
        candidate = candidate_by_id.get(str(link.get("candidate_id") or ""))
        raw_sequence = str(candidate.get("normalized_sequence")) if candidate else None
        direction = str(candidate.get("direction") or "unknown") if candidate else "unknown"
        sequence_source = "explicit_in_protocol" if raw_sequence else "not_shown_in_protocol"
        memory_id = None
        evidence = _candidate_evidence(candidate)
        components = _link_components(link, candidate, candidate_by_id, candidates_by_block)
        aliases: list[str] = []
        assembled_from_components = False
        link_notes = link.get("notes") if isinstance(link.get("notes"), str) else None
        if components:
            raw_sequence = None
            sequence, components = _sequence_components_for_join(name, components, link_notes)
            if sequence:
                assembled_from_components = True
                name = _canonical_component_join_name(name, sequence, components, link_notes)
            sequence_source = "explicit_in_protocol"
            name, aliases = _double_stranded_name(name, components, memory)
        else:
            sequence, components = semantic_sequence_and_components(
                raw_sequence,
                name,
                str(candidate.get("nearby_text") or "") if candidate else "",
            )
        if candidate:
            memory_match = _memory_match_for_sequence(raw_sequence, direction, memory)
            if not memory_match:
                memory_match = _memory_similarity_match_for_sequence(raw_sequence, direction, memory, name)
            if memory_match:
                name = str(memory_match.get("name") or name)
                aliases = [str(alias) for alias in memory_match.get("aliases") or [] if str(alias)]
                memory_sequence, memory_components = semantic_sequence_and_components(str(memory_match.get("sequence") or ""), name)
                if memory_sequence:
                    sequence = memory_sequence
                    components = memory_components
                direction = str(memory_match.get("direction") or direction)
                memory_id = _memory_item_id(memory_match)
        else:
            memory_match = _agentic_memory_match_for_name(name, memory)
            if memory_match:
                sequence, components = semantic_sequence_and_components(str(memory_match["sequence"]), str(memory_match.get("name") or name))
                name = str(memory_match.get("name") or name)
                aliases = [str(alias) for alias in memory_match.get("aliases") or [] if str(alias)]
                direction = str(memory_match.get("direction") or "unknown")
                sequence_source = "memory_completed"
                memory_id = _memory_item_id(memory_match)
        assay = assay_context_for_link(
            name,
            candidate,
            candidate_sequence_counts.get(sequence_key(raw_sequence), 0) if candidate else 1,
        )
        if assay == "vdj" and is_generic_directional_primer_name(name):
            name = f"VDJ {name}"
        name_key = display_name_key(name)
        if memory.get("oligo_nodes") and re.search(r"\bdual index kit\b.*\bprimer\b", name, flags=re.I):
            continue
        if not memory_id and is_construct_or_product_name(name):
            continue
        if not sequence and not components and not memory_id:
            continue
        if not sequence and not components and name_key in sequenced_name_keys:
            continue
        component_key = "|".join(f"{component.role}:{sequence_key(component.sequence)}" for component in components)
        key = (name_key, sequence_key(sequence) or component_key)
        if key in seen:
            continue
        seen.add(key)
        if sequence:
            sequenced_name_keys.add(name_key)
        kind = str(link.get("kind") or infer_kind(name, sequence, len(components)))
        if assembled_from_components:
            kind = "assembled"
            direction = "5_to_3"
        elif components and not sequence:
            kind = "double_stranded"
            direction = "5_to_3"
        role = str(link.get("role") or infer_role(name))
        if memory_id and candidate:
            memory_role = _memory_item_role(memory_match) if memory_match else "unknown"
            if memory_role != "unknown":
                role = memory_role
        if components and not sequence:
            role = "adapter" if role == "unknown" else role
        if role not in set(ROLE_TERMS) | {"unknown"}:
            role = infer_role(name)
        oligos.append(
            Oligo(
                oligo_id=f"oligo_{slug(name)}" if key[0] else f"oligo_candidate_{index}",
                protocol_id=protocol_id,
                protocol_name=protocol_name,
                name=name,
                aliases=aliases,
                role=role,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                sequence=sequence,
                direction=direction if direction in {"5_to_3", "3_to_5", "unknown"} else "unknown",  # type: ignore[arg-type]
                components=components,
                sequence_source=sequence_source,  # type: ignore[arg-type]
                memory_id=memory_id,
                evidence=evidence,
                notes=annotate_assay_note(link.get("notes") if isinstance(link.get("notes"), str) else None, assay),
            )
        )

    oligos = _collapse_barcoded_series(protocol_id, protocol_name, oligos, memory)
    oligos.extend(_supported_memory_oligos(protocol_id, protocol_name, source_files, oligos, sequence_candidates, memory, protocol_brief))
    component_source_text_key = _source_text_key_for_support(oligos, sequence_candidates, protocol_brief)
    _complete_assembled_components_from_memory(oligos, memory, component_source_text_key)
    oligos.extend(_supported_component_memory_oligos(protocol_id, protocol_name, oligos, sequence_candidates, memory, protocol_brief))
    oligos = _prune_shadowed_oligos(oligos)
    _strip_output_aliases(oligos)
    return ProtocolOligoSet(
        protocol_id=protocol_id,
        protocol_name=protocol_name,
        split=split,  # type: ignore[arg-type]
        source_files=source_files,
        oligos=oligos,
    )


def verify_prediction(prediction: ProtocolOligoSet, sequence_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_sequences = {sequence_key(candidate.get("normalized_sequence")) for candidate in sequence_candidates}
    allowed_sequences.update(
        semantic_sequence_key(
            str(candidate.get("normalized_sequence") or ""),
            str(candidate.get("name_hint") or ""),
            str(candidate.get("nearby_text") or ""),
        )
        for candidate in sequence_candidates
    )
    findings: list[dict[str, Any]] = []
    for oligo in prediction.oligos:
        key = sequence_key(oligo.sequence)
        if oligo.sequence and oligo.sequence_source == "memory_completed" and not oligo.memory_id:
            findings.append({"oligo_id": oligo.oligo_id, "severity": "error", "message": "memory_completed oligo is missing memory_id"})
        if (
            oligo.sequence
            and oligo.sequence_source in {"explicit_in_protocol", "explicit_in_linked_table"}
            and key not in allowed_sequences
            and "collapsed" not in str(oligo.notes or "").lower()
        ):
            findings.append({"oligo_id": oligo.oligo_id, "severity": "error", "message": "sequence not present in deterministic candidates"})
        if oligo.sequence and oligo.sequence_source == "explicit_in_protocol" and not oligo.evidence:
            findings.append({"oligo_id": oligo.oligo_id, "severity": "warning", "message": "explicit sequence has no evidence"})
    return findings


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def sequence_similarity(left: str | None, right: str | None) -> float:
    left_key = sequence_key(left)
    right_key = sequence_key(right)
    if not left_key and not right_key:
        return 1.0
    denominator = max(len(left_key), len(right_key))
    if denominator == 0:
        return 1.0
    return 1.0 - (edit_distance(left_key, right_key) / denominator)


def compare_to_ground_truth(
    prediction: ProtocolOligoSet,
    ground_truth: ProtocolOligoSet,
    *,
    sequence_similarity_threshold: float = 0.9,
) -> dict[str, Any]:
    gt_by_name = {display_name_key(oligo.name): oligo for oligo in ground_truth.oligos}

    def is_auxiliary_component_prediction(oligo: Oligo) -> bool:
        if display_name_key(oligo.name) in gt_by_name:
            return False
        sequence = sequence_key(oligo.sequence)
        if not sequence:
            return False
        for parent in prediction.oligos:
            if parent.oligo_id == oligo.oligo_id:
                continue
            for component in parent.components:
                if sequence and sequence == sequence_key(component.sequence):
                    return True
        return False

    auxiliary_component_oligos = [oligo for oligo in prediction.oligos if is_auxiliary_component_prediction(oligo)]
    auxiliary_ids = {oligo.oligo_id for oligo in auxiliary_component_oligos}
    comparable_predictions = [oligo for oligo in prediction.oligos if not is_vdj_oligo(oligo) and oligo.oligo_id not in auxiliary_ids]
    allowed_extra_oligos = [oligo for oligo in prediction.oligos if is_vdj_oligo(oligo)]
    pred_by_name = {display_name_key(oligo.name): oligo for oligo in comparable_predictions}
    matched_names = sorted(set(pred_by_name) & set(gt_by_name))
    missed_names = sorted(set(gt_by_name) - set(pred_by_name))
    extra_names = sorted(set(pred_by_name) - set(gt_by_name))
    allowed_extra_names = sorted({display_name_key(oligo.name) for oligo in allowed_extra_oligos if display_name_key(oligo.name)})

    exact_matches = [
        name
        for name in matched_names
        if semantic_sequence_key(pred_by_name[name].sequence, pred_by_name[name].name)
        == semantic_sequence_key(gt_by_name[name].sequence, gt_by_name[name].name)
    ]
    sequence_similarities: dict[str, float] = {}
    tolerated_sequence_variants: list[dict[str, Any]] = []
    for name in matched_names:
        pred = pred_by_name[name]
        gt = gt_by_name[name]
        similarity = sequence_similarity(
            semantic_sequence_key(pred.sequence, pred.name),
            semantic_sequence_key(gt.sequence, gt.name),
        )
        sequence_similarities[name] = similarity
        if name not in exact_matches and similarity >= sequence_similarity_threshold:
            tolerated_sequence_variants.append(
                {
                    "gt_name": gt.name,
                    "pred_name": pred.name,
                    "gt_sequence": gt.sequence,
                    "pred_sequence": pred.sequence,
                    "sequence_similarity": similarity,
                    "notes": "Sequence differs from ground truth but is within the configured similarity threshold.",
                }
            )
    matched_similarity_sum = sum(sequence_similarities.values())

    gt_sequence_records = [
        (name, gt_by_name[name], semantic_sequence_key(gt_by_name[name].sequence, gt_by_name[name].name))
        for name in sorted(gt_by_name)
        if semantic_sequence_key(gt_by_name[name].sequence, gt_by_name[name].name)
    ]
    pred_sequence_records = [
        (display_name_key(oligo.name), oligo, semantic_sequence_key(oligo.sequence, oligo.name))
        for oligo in comparable_predictions
        if semantic_sequence_key(oligo.sequence, oligo.name)
    ]
    best_sequence_matches: list[dict[str, Any]] = []
    for _pred_name_key, pred, pred_sequence_key in pred_sequence_records:
        best_gt: Oligo | None = None
        best_similarity = 0.0
        for _gt_name_key, gt, gt_sequence_key in gt_sequence_records:
            similarity = sequence_similarity(pred_sequence_key, gt_sequence_key)
            if similarity > best_similarity:
                best_similarity = similarity
                best_gt = gt
        best_sequence_matches.append(
            {
                "pred_name": pred.name,
                "pred_sequence": pred.sequence,
                "best_gt_name": best_gt.name if best_gt else None,
                "best_gt_sequence": best_gt.sequence if best_gt else None,
                "sequence_similarity": best_similarity,
            }
        )
    best_similarity_sum = sum(float(item["sequence_similarity"]) for item in best_sequence_matches)

    failures: list[dict[str, Any]] = []
    for idx, name in enumerate(missed_names, start=1):
        gt = gt_by_name[name]
        failures.append(
            {
                "failure_id": f"F{len(failures) + 1:03d}",
                "type": "missed_oligo",
                "gt_name": gt.name,
                "pred_name": None,
                "gt_sequence": gt.sequence,
                "pred_sequence": None,
                "suggested_fix_target": "llm_rules / candidate_scanner",
                "notes": "Ground-truth oligo name was not present in prediction.",
            }
        )
    for name in matched_names:
        gt = gt_by_name[name]
        pred = pred_by_name[name]
        if semantic_sequence_key(gt.sequence, gt.name) == semantic_sequence_key(pred.sequence, pred.name):
            continue
        similarity = sequence_similarities.get(name, 0.0)
        if similarity >= sequence_similarity_threshold:
            continue
        failures.append(
            {
                "failure_id": f"F{len(failures) + 1:03d}",
                "type": "wrong_sequence",
                "gt_name": gt.name,
                "pred_name": pred.name,
                "gt_sequence": gt.sequence,
                "pred_sequence": pred.sequence,
                "sequence_similarity": similarity,
                "suggested_fix_target": "candidate_scanner" if pred.sequence else "memory / resolver",
                "notes": "Matched oligo name but sequence did not exactly match ground truth.",
            }
        )
    for name in extra_names:
        pred = pred_by_name[name]
        failures.append(
            {
                "failure_id": f"F{len(failures) + 1:03d}",
                "type": "extra_oligo",
                "gt_name": None,
                "pred_name": pred.name,
                "gt_sequence": None,
                "pred_sequence": pred.sequence,
                "suggested_fix_target": "resolver / verifier",
                "notes": "Prediction has no matched ground-truth oligo name.",
            }
        )

    metrics = {
        "oligo_name_recall": {
            "value": len(matched_names) / len(gt_by_name) if gt_by_name else 1.0,
            "numerator": len(matched_names),
            "denominator": len(gt_by_name),
        },
        "oligo_name_precision": {
            "value": len(matched_names) / len(pred_by_name) if pred_by_name else (1.0 if not gt_by_name else 0.0),
            "numerator": len(matched_names),
            "denominator": len(pred_by_name),
        },
        "sequence_exact_match": {
            "value": len(exact_matches) / len(matched_names) if matched_names else (1.0 if not gt_by_name else 0.0),
            "numerator": len(exact_matches),
            "denominator": len(matched_names),
        },
        "sequence_similarity_pass": {
            "value": (
                sum(1 for similarity in sequence_similarities.values() if similarity >= sequence_similarity_threshold) / len(matched_names)
                if matched_names
                else (1.0 if not gt_by_name else 0.0)
            ),
            "numerator": sum(1 for similarity in sequence_similarities.values() if similarity >= sequence_similarity_threshold),
            "denominator": len(matched_names),
            "threshold": sequence_similarity_threshold,
        },
        "matched_sequence_similarity_mean": {
            "value": (
                matched_similarity_sum / len(matched_names)
                if matched_names
                else (1.0 if not gt_by_name else 0.0)
            ),
            "numerator": matched_similarity_sum,
            "denominator": len(matched_names),
        },
        "sequence_best_match_mean": {
            "value": (
                best_similarity_sum / len(best_sequence_matches)
                if best_sequence_matches
                else (1.0 if not gt_sequence_records else 0.0)
            ),
            "numerator": best_similarity_sum,
            "denominator": len(best_sequence_matches),
        },
        "allowed_extra_vdj": {
            "value": len(allowed_extra_oligos),
            "numerator": len(allowed_extra_oligos),
            "denominator": len(prediction.oligos),
        },
    }
    return {
        "metrics": metrics,
        "failures": failures,
        "matched_names": matched_names,
        "missed_names": missed_names,
        "extra_names": extra_names,
        "allowed_extra_names": allowed_extra_names,
        "ignored_auxiliary_component_names": sorted(oligo.name for oligo in auxiliary_component_oligos),
        "tolerated_sequence_variants": tolerated_sequence_variants,
        "sequence_best_matches": best_sequence_matches,
        "sequence_similarity_threshold": sequence_similarity_threshold,
    }


def aggregate_eval_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = ["oligo_name_recall", "oligo_name_precision", "matched_sequence_similarity_mean", "sequence_best_match_mean"]
    mean_similarity_metrics = {"matched_sequence_similarity_mean", "sequence_best_match_mean"}
    aggregate_metrics: dict[str, dict[str, float | int]] = {}
    for metric_name in metric_names:
        numerator = sum(float(item["metrics"][metric_name]["numerator"]) for item in results)
        denominator = sum(int(item["metrics"][metric_name]["denominator"]) for item in results)
        values = [float(item["metrics"][metric_name]["value"]) for item in results]
        display_numerator: float | int = numerator
        display_denominator: float | int = denominator
        if metric_name not in mean_similarity_metrics:
            display_numerator = int(numerator)
            display_denominator = int(denominator)
        aggregate_metrics[metric_name] = {
            "value": numerator / denominator if denominator else 1.0,
            "numerator": display_numerator,
            "denominator": display_denominator,
            "macro_average": sum(values) / len(values) if values else 0.0,
        }

    failure_counts: dict[str, int] = {}
    for item in results:
        for failure_type, count in (item.get("failure_types") or {}).items():
            failure_counts[str(failure_type)] = failure_counts.get(str(failure_type), 0) + int(count)

    return {
        "metrics": aggregate_metrics,
        "failure_count": sum(int(item.get("failure_count") or 0) for item in results),
        "failure_types": dict(sorted(failure_counts.items())),
    }


def eval_summary_markdown(summary: dict[str, Any]) -> str:
    aggregate = summary.get("aggregate") or {}
    metrics = aggregate.get("metrics") or {}
    failure_types = aggregate.get("failure_types") or {}

    def metric_numerator(metric_name: str, metric: dict[str, Any]) -> str:
        if metric_name in {"matched_sequence_similarity_mean", "sequence_best_match_mean"}:
            return f"{float(metric.get('numerator') or 0):.4f}"
        return str(int(metric.get("numerator") or 0))

    lines = [
        f"# {str(summary.get('split') or '').title()} Summary",
        "",
        f"- run_id: {summary.get('run_id')}",
        f"- split_file: {summary.get('split_file') or ''}",
        f"- protocols: {summary.get('count')}",
        f"- use_memory: {summary.get('use_memory')}",
        f"- memory_path: {summary.get('memory_path') or ''}",
        "",
        "## Aggregate Metrics",
        "",
        "| metric | value | numerator | denominator | macro_average |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for metric_name in ["oligo_name_recall", "oligo_name_precision", "matched_sequence_similarity_mean", "sequence_best_match_mean"]:
        metric = metrics.get(metric_name) or {}
        lines.append(
            f"| {metric_name} | {float(metric.get('value') or 0):.4f} | "
            f"{metric_numerator(metric_name, metric)} | {int(metric.get('denominator') or 0)} | "
            f"{float(metric.get('macro_average') or 0):.4f} |"
        )
    lines.extend(["", "## Failure Counts", ""])
    if failure_types:
        for failure_type, count in sorted(failure_types.items()):
            lines.append(f"- {failure_type}: {count}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Protocols",
            "",
            "| protocol_id | recall | precision | matched_seq_mean | seq_best_mean | failures |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summary.get("results") or []:
        item_metrics = item["metrics"]
        lines.append(
            f"| {item['protocol_id']} | "
            f"{float(item_metrics['oligo_name_recall']['value']):.4f} | "
            f"{float(item_metrics['oligo_name_precision']['value']):.4f} | "
            f"{float(item_metrics.get('matched_sequence_similarity_mean', {}).get('value') or 0):.4f} | "
            f"{float(item_metrics.get('sequence_best_match_mean', {}).get('value') or 0):.4f} | "
            f"{int(item.get('failure_count') or 0)} |"
        )
    return "\n".join(lines) + "\n"


def prediction_report(protocol: ProtocolOligoSet, run_dir: Path, linker_trace: dict[str, Any]) -> str:
    lines = [
        f"Protocol: {protocol.protocol_id}",
        f"Split: {protocol.split}",
        "",
        f"Predicted oligos: {len(protocol.oligos)}",
        "",
        "Chunks:",
        f"  {run_dir / 'chunks.json'}",
        "",
        "Prediction:",
        f"  {run_dir / 'prediction.json'}",
        "Trace:",
        f"  {run_dir / 'trace.json'}",
    ]
    linker_status = str(linker_trace.get("status") or "unknown")
    if linker_status:
        lines.extend(["", f"Linker: {linker_status}"])
    return "\n".join(lines)


def run_improve(
    protocol_id: str,
    input_path: Path,
    out: Path,
    *,
    split: str = "train",
    use_memory: bool = False,
    memory_path: Path | None = None,
    run_identifier: str | None = None,
    nested_output: bool = False,
) -> dict[str, Any]:
    if split not in {"train", "eval", "test"}:
        raise ValueError("--split must be one of train, eval, or test")
    protocol_name = protocol_id
    memory = filter_memory_for_protocol(load_runtime_memory(memory_path, use_memory), protocol_id, split)

    rid = run_identifier or run_id()
    out_root = out.expanduser().resolve()
    run_dir = out_root / rid / protocol_id if nested_output else out_root
    run_dir.mkdir(parents=True, exist_ok=True)

    blocks, source_file_list = parse_input_blocks(input_path)
    if not source_file_list:
        source_file_list = [str(path) for path in source_files(input_path)]
    chunks = build_evidence_chunks(blocks)
    chunks_payload = {
        "protocol_id": protocol_id,
        "protocol_name": protocol_name,
        "source_files": source_file_list,
        "block_count": len(blocks),
        "chunk_count": len(chunks),
        "chunk_index": chunk_index(chunks),
        "chunks": chunks,
    }
    chunks_path = run_dir / "chunks.json"
    write_json(chunks_path, chunks_payload)
    name_mentions = scan_name_mentions(blocks)
    sequence_candidates = scan_sequence_candidates(blocks)
    links, linker_trace, sequence_candidates = run_linker(
        protocol_id,
        protocol_name,
        blocks,
        chunks,
        name_mentions,
        sequence_candidates,
        memory,
        chunks_path,
        source_file_list,
    )
    linker_findings = verify_links(links, sequence_candidates)
    protocol_brief = sanitize_protocol_brief(linker_trace.get("protocol_brief"))
    prediction = resolve_oligos(protocol_id, protocol_name, split, source_file_list, links, sequence_candidates, memory, protocol_brief)
    prediction.summary = protocol_brief["summary"]
    prediction.major_steps = protocol_brief["major_steps"]
    verifier_findings = verify_prediction(prediction, sequence_candidates)

    prediction_payload = prediction_output_payload(prediction)
    trace = {
        "run_id": rid,
        "protocol_id": protocol_id,
        "protocol_name": protocol_name,
        "split": split,
        "input": str(input_path),
        "use_memory": use_memory,
        "memory_path": str(memory_path.expanduser().resolve()) if memory_path else None,
        "memory_source": memory.get("source"),
        "memory": memory_trace(memory),
        "chunks_path": str(chunks_path),
        "chunk_count": len(chunks),
        "blocks": blocks,
        "name_mentions": name_mentions,
        "sequence_candidates": sequence_candidates,
        "links": links,
        "linker": linker_trace,
        "linker_findings": linker_findings,
        "verifier_findings": verifier_findings,
    }

    write_json(run_dir / "prediction.json", prediction_payload)
    write_json(run_dir / "trace.json", trace)

    report = prediction_report(prediction, run_dir, linker_trace)
    return {
        "run_id": rid,
        "run_dir": str(run_dir),
        "prediction": prediction_payload,
        "trace": trace,
        "report": report,
    }


def run_eval_split(
    split: str,
    *,
    protocol_root: Path,
    split_file: Path | None = None,
    limit: int | None = None,
    frozen: bool = False,
    out: Path | None = None,
    use_memory: bool = False,
    memory_path: Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if split == "test" and not frozen:
        raise ValueError("test split requires --frozen")
    selected_split_file = split_file.expanduser().resolve() if split_file else default_split_file(protocol_root)
    items = sorted(protocol_entries(protocol_root, split=split, split_file=selected_split_file), key=lambda item: str(item.get("protocol_id") or ""))
    if limit is not None:
        items = items[:limit]
    if selected_split_file is not None and not items:
        raise ValueError(f"split file selected no protocols for split {split}: {selected_split_file}")
    eval_run_id = run_id()
    out_root = out or protocol_root / "training" / "eval-runs"
    results = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        protocol_id = str(item["protocol_id"])
        protocol_dir = Path(str(item["protocol_dir"]))
        ground_truth_json = Path(str(item["ground_truth_json"]))
        if progress:
            progress({"event": "protocol_start", "index": index, "total": total, "protocol_id": protocol_id})
        result = run_improve(
            protocol_id,
            protocol_dir,
            out_root,
            split=split,
            use_memory=use_memory,
            memory_path=memory_path,
            run_identifier=eval_run_id,
            nested_output=True,
        )
        ground_truth = load_ground_truth(ground_truth_json, protocol_id, split)
        prediction = ProtocolOligoSet.model_validate(result["prediction"])
        comparison = compare_to_ground_truth(prediction, ground_truth)
        failure_types: dict[str, int] = {}
        for failure in comparison["failures"]:
            failure_type = str(failure.get("type") or "unknown")
            failure_types[failure_type] = failure_types.get(failure_type, 0) + 1
        results.append(
            {
                "protocol_id": protocol_id,
                "run_dir": result["run_dir"],
                "metrics": comparison["metrics"],
                "failure_count": len(comparison["failures"]),
                "failure_types": dict(sorted(failure_types.items())),
            }
        )
        if progress:
            progress(
                {
                    "event": "protocol_result",
                    "index": index,
                    "total": total,
                    "protocol_id": protocol_id,
                    "metrics": comparison["metrics"],
                    "failure_count": len(comparison["failures"]),
                    "failure_types": dict(sorted(failure_types.items())),
                }
            )
    aggregate = aggregate_eval_results(results)
    summary = {
        "run_id": eval_run_id,
        "split": split,
        "split_file": str(selected_split_file) if selected_split_file else None,
        "count": len(results),
        "use_memory": use_memory,
        "memory_path": str(memory_path.expanduser().resolve()) if memory_path else None,
        "aggregate": aggregate,
        "results": results,
    }
    summary_dir = out_root.expanduser().resolve() / eval_run_id
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_json_path = summary_dir / f"{split}_summary.json"
    summary_md_path = summary_dir / "summary.md"
    latest_json_path = out_root.expanduser().resolve() / "latest_summary.json"
    latest_md_path = out_root.expanduser().resolve() / "latest_summary.md"
    summary["summary_dir"] = str(summary_dir)
    summary["summary_json"] = str(summary_json_path)
    summary["summary_markdown"] = str(summary_md_path)
    summary["latest_summary_json"] = str(latest_json_path)
    summary["latest_summary_markdown"] = str(latest_md_path)
    write_json(summary_json_path, summary)
    (summary_dir / "summary.md").write_text(eval_summary_markdown(summary), encoding="utf-8")
    write_json(latest_json_path, summary)
    latest_md_path.write_text(eval_summary_markdown(summary), encoding="utf-8")
    return summary

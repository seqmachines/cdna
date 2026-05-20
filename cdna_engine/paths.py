from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
OUTPUTS_DIR = REPO_ROOT / "outputs"
SEQUENCE_INVENTORY_DB = REPO_ROOT / "data" / "sequence_inventory" / "oligos.tsv"

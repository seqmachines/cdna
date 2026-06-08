#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cdna_engine.oligos.sequence_inventory import extract_sequence_inventory, load_inventory_rows, main


__all__ = ["extract_sequence_inventory", "load_inventory_rows", "main"]


if __name__ == "__main__":
    raise SystemExit(main())

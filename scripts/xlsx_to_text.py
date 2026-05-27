#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from cdna_engine.io import xlsx_to_text


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: xlsx_to_text.py <xlsx_path>", file=sys.stderr)
        return 2
    print(xlsx_to_text(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

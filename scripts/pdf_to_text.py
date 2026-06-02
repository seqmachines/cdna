#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: pdf_to_text.py <pdf_path>", file=sys.stderr)
        return 2

    try:
        from pypdf import PdfReader
    except Exception:
        print(
            "Missing Python dependency pypdf. Run: python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 3

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        pages.append(f"[[PAGE {idx}]]\n{page.extract_text() or ''}")

    print("\n\n".join(pages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

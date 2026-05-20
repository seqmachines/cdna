from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader


def html_to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "\n", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|section|article|li|tr|h[1-6]|pre|br)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    entities = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&#39;": "'",
        "&quot;": '"',
    }
    for source, replacement in entities.items():
        text = text.replace(source, replacement)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+\n", "\n\n", text)
    return text.strip()


def pdf_to_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for idx, page in enumerate(reader.pages, start=1):
        pages.append(f"[[PAGE {idx}]]\n{page.extract_text() or ''}")
    return "\n\n".join(pages)


def prepare_protocol_text(input_path: Path) -> str:
    input_path = input_path.expanduser().resolve()
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_text(input_path)
    raw = input_path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".html", ".htm"}:
        return html_to_text(raw)
    return raw


def output_slug(source: str | Path) -> str:
    value = Path(source).name
    value = re.sub(r"\.[A-Za-z0-9]+$", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "protocol"

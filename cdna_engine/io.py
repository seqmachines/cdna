from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

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


def _xml_text(element: ET.Element) -> str:
    return "".join(element.itertext())


def xlsx_to_text(path: Path) -> str:
    namespace = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                _xml_text(item).strip()
                for item in root.findall(".//main:si", namespace)
            ]

        rel_targets: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in archive.namelist():
            rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rel_root.findall("pkgrel:Relationship", namespace):
                rel_id = rel.attrib.get("Id")
                target = rel.attrib.get("Target")
                if rel_id and target:
                    rel_targets[rel_id] = target

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        parts: list[str] = []
        for index, sheet in enumerate(
            workbook.findall(".//main:sheets/main:sheet", namespace),
            start=1,
        ):
            name = sheet.attrib.get("name") or f"Sheet {index}"
            rel_id = sheet.attrib.get(f"{{{namespace['rel']}}}id")
            target = rel_targets.get(rel_id or "", f"worksheets/sheet{index}.xml")
            sheet_path = "xl/" + target.lstrip("/")
            if sheet_path not in archive.namelist():
                continue
            root = ET.fromstring(archive.read(sheet_path))
            rows: list[str] = [f"[[SHEET: {name}]]"]
            for row in root.findall(".//main:sheetData/main:row", namespace):
                values: list[str] = []
                for cell in row.findall("main:c", namespace):
                    cell_type = cell.attrib.get("t")
                    value = ""
                    if cell_type == "inlineStr":
                        inline = cell.find("main:is", namespace)
                        value = _xml_text(inline).strip() if inline is not None else ""
                    else:
                        raw_value = cell.findtext("main:v", default="", namespaces=namespace)
                        if cell_type == "s" and raw_value:
                            try:
                                value = shared_strings[int(raw_value)]
                            except (IndexError, ValueError):
                                value = raw_value
                        else:
                            value = raw_value.strip()
                    values.append(value)
                if any(values):
                    rows.append("\t".join(values))
            parts.append("\n".join(rows))
    return "\n\n".join(parts)


def prepare_protocol_text(input_path: Path) -> str:
    input_path = input_path.expanduser().resolve()
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_text(input_path)
    if suffix == ".xlsx":
        return xlsx_to_text(input_path)
    raw = input_path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".html", ".htm"}:
        return html_to_text(raw)
    return raw


def output_slug(source: str | Path) -> str:
    value = Path(source).name
    value = re.sub(r"\.[A-Za-z0-9]+$", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "protocol"

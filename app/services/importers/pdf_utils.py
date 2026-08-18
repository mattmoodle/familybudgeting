from __future__ import annotations

import re
from pathlib import Path
from pypdf import PdfReader


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        parts.append(text)
    text = "\n".join(parts)
    if not text.strip():
        raise ValueError("PDF contains no extractable text. Scanned PDFs require the optional offline OCR adapter.")
    return text


def compact_spaces(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value).strip()

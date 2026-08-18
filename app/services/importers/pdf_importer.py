import re
from pathlib import Path

from pypdf import PdfReader

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date

# Generic fallback for text-based statements. Institution-specific parsers can be added later.
LINE_RE = re.compile(
    r"(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})\s+(?P<body>.+?)\s+(?P<amount>-?[\d.]+,\d{2}|-?[\d,]+\.\d{2})\s*$"
)


class PdfImporter(StatementImporter):
    def parse(self, path: Path) -> list[ImportedRow]:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            raise ValueError("PDF contains no extractable text. Scanned PDFs require a custom offline OCR adapter.")

        result: list[ImportedRow] = []
        for line in text.splitlines():
            match = LINE_RE.search(" ".join(line.split()))
            if not match:
                continue
            booked = parse_date(match.group("date"))
            amount = parse_amount(match.group("amount"))
            if booked is None or amount is None:
                continue
            body = match.group("body").strip()
            result.append(ImportedRow(booked, body, amount, raw_data=line))
        if not result:
            raise ValueError(
                "No transaction rows recognized in PDF. Add an institution-specific parser under app/services/importers/."
            )
        return result

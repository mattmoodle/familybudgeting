from __future__ import annotations

import re
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date
from app.services.importers.pdf_utils import extract_pdf_text

# "Movimenti Globali" export: account token + accounting date + value date + signed amount + cause + description.
GLOBAL_ROW = re.compile(
    r"(?P<account>Z\d{20,})\s+"
    r"(?P<booked>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<value>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<amount>-?[\d.]+,\d{2})\s+"
    # Some exports repeat account-holder text before the next account token.
    # Match that layout generically: no customer name is embedded in the parser.
    r"(?P<body>.*?)(?=(?:(?:[A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý'.,-]*\s+){0,8})?Z\d{20,}\s+\d{2}/\d{2}/\d{4}|\Z)",
    re.I | re.S,
)


class BccBankPdfImporter(StatementImporter):
    @staticmethod
    def matches(text: str) -> bool:
        upper = text.upper()
        return "BANCA DI CREDITO COOPERATIVO DI ROMA" in upper and ("MOVIMENTI GLOBALI" in upper or "DETTAGLIO MOVIMENTI" in upper)

    def parse(self, path: Path) -> list[ImportedRow]:
        return self.parse_text(extract_pdf_text(path))

    def parse_text(self, text: str) -> list[ImportedRow]:
        # Flatten wrapped rows while leaving account/date separators recognizable.
        normalized = re.sub(r"\s+", " ", text)
        rows: list[ImportedRow] = []
        for m in GLOBAL_ROW.finditer(normalized):
            booked = parse_date(m.group("booked"))
            value = parse_date(m.group("value"))
            amount = parse_amount(m.group("amount"))
            if not booked or amount is None:
                continue
            body = re.sub(r"\s+", " ", m.group("body")).strip()
            rows.append(ImportedRow(booked, body, amount, value_on=value, raw_data=m.group(0)))
        if not rows:
            raise ValueError(
                "BCC statement recognized but this PDF layout did not expose signed debit/credit values. "
                "Prefer the BCC 'Movimenti Globali' export (PDF/CSV/XLSX) for lossless import."
            )
        return rows

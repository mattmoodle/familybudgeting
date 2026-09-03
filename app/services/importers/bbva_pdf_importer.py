from __future__ import annotations

import re
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date
from app.services.importers.pdf_utils import compact_spaces, extract_pdf_text


# BBVA "Ultime transazioni" PDFs have one transaction header row followed by a
# "Data valuta" row. The payment detail can wrap on further lines.
ROW_RE = re.compile(
    r"^(?P<booked>\d{2}/\d{2}/\d{4})\s+(?P<cause>.+?)\s+"
    r"(?P<amount>-?[\d.]+,\d{2})\s+.*?[\d.]+,\d{2}\s+EUR$",
    re.I,
)
VALUE_RE = re.compile(r"^Data valuta:\s*(?P<value>\d{2}/\d{2}/\d{4})(?:\s+(?P<body>.*))?$", re.I)
SKIP_RE = re.compile(r"^(?:Ultime transazioni|Data\s+Causale\s+Importo\s+Saldo|\d+/\d+)$", re.I)


class BbvaPdfImporter(StatementImporter):
    @staticmethod
    def matches(text: str) -> bool:
        upper = text.upper()
        return "ULTIME TRANSAZIONI" in upper and "DATA VALUTA:" in upper and "CAUSALE" in upper and "SALDO" in upper

    def parse(self, path: Path) -> list[ImportedRow]:
        return self.parse_text(extract_pdf_text(path))

    def parse_text(self, text: str) -> list[ImportedRow]:
        lines = [compact_spaces(line) for line in text.splitlines()]
        rows: list[ImportedRow] = []
        i = 0
        while i < len(lines):
            match = ROW_RE.match(lines[i])
            if not match:
                i += 1
                continue
            booked = parse_date(match.group("booked"))
            amount = parse_amount(match.group("amount"))
            if not booked or amount is None:
                i += 1
                continue

            value_on = None
            body_parts = [match.group("cause")]
            j = i + 1
            while j < len(lines):
                next_row = ROW_RE.match(lines[j])
                if next_row:
                    break
                line = lines[j]
                value_match = VALUE_RE.match(line)
                if value_match:
                    value_on = parse_date(value_match.group("value"))
                    if value_match.group("body"):
                        body_parts.append(value_match.group("body"))
                elif line and not SKIP_RE.match(line):
                    body_parts.append(line)
                j += 1

            description = " ".join(part for part in body_parts if part).strip()
            rows.append(ImportedRow(booked, description, amount, value_on=value_on, raw_data=" ".join(body_parts)))
            i = j

        if not rows:
            raise ValueError("BBVA statement recognized, but no transaction rows were parsed")
        return rows

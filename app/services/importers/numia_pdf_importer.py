from __future__ import annotations

import re
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date
from app.services.importers.pdf_utils import extract_pdf_text

TX_START = re.compile(r"(?m)^\s*(?P<purchase>\d{2}/\d{2}/\d{4})\s+(?P<booked>\d{2}/\d{2}/\d{4})\s+(?P<rest>.+)$")
AMOUNT_AT_END = re.compile(r"(?P<amount>-?[\d.]+,\d{2})\s*$")
FOREIGN_AMOUNT = re.compile(r"\b[\d.]+(?:,\d+)?\s+(?:USD|JPY|GBP|CHF)\b", re.I)
CURRENT_ROW = re.compile(
    r"(?P<purchase>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<booked>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<body>.*?)\s+"
    r"(?P<original>-?[\d.]+(?:,\d{2})?)\s+"
    r"(?P<amount>-?[\d.]+(?:,\d{2})?)\s+"
    r"(?P<commission>-?[\d.]+(?:,\d{2})?)\s+"
    r"(?P<currency>[A-Z]{3})",
    re.S,
)


class NumiaCardPdfImporter(StatementImporter):
    """Parser for BCC/Numia credit-card statements.

    Numia statements present purchases as positive amounts and refunds/payments as
    negative amounts. The application convention is the opposite (expenses < 0),
    therefore every statement amount is inverted.
    """

    @staticmethod
    def matches(text: str) -> bool:
        upper = text.upper()
        return "LISTA MOVIMENTI" in upper and ("CREDIT MC" in upper or "NUMIA" in upper or "PLAFOND" in upper)

    def parse(self, path: Path) -> list[ImportedRow]:
        return self.parse_text(extract_pdf_text(path))

    def parse_text(self, text: str) -> list[ImportedRow]:
        current_rows = self._parse_current_layout(text)
        if current_rows:
            return current_rows
        lines = text.splitlines()
        rows: list[ImportedRow] = []
        i = 0
        while i < len(lines):
            line = " ".join(lines[i].split())
            m = TX_START.match(line)
            if not m:
                i += 1
                continue
            purchase = parse_date(m.group("purchase"))
            booked = parse_date(m.group("booked"))
            rest = m.group("rest")
            # Some FX transactions put the EUR amount on the following line.
            chunks = [rest]
            j = i + 1
            while j < len(lines) and j <= i + 2:
                nxt = " ".join(lines[j].split())
                if TX_START.match(nxt) or not nxt:
                    break
                chunks.append(nxt)
                if AMOUNT_AT_END.search(nxt):
                    break
                j += 1
            combined = " ".join(chunks)
            amount_m = AMOUNT_AT_END.search(combined)
            if purchase and booked and amount_m:
                raw_amount = parse_amount(amount_m.group("amount"))
                if raw_amount is not None:
                    description = combined[: amount_m.start()].strip()
                    description = FOREIGN_AMOUNT.sub(lambda x: x.group(0), description).strip()
                    # Exclude statement summaries accidentally matching transaction shape.
                    if not description.upper().startswith(("SALDO ", "TRANSAZIONI ")):
                        rows.append(
                            ImportedRow(
                                booked_on=booked,
                                value_on=purchase,
                                description=description,
                                amount=-raw_amount,
                                raw_data=combined,
                            )
                        )
            i = max(i + 1, j if j > i + 1 else i + 1)
        if not rows:
            raise ValueError("Numia/BCC card statement recognized, but no transactions were parsed")
        return rows

    @staticmethod
    def _parse_current_layout(text: str) -> list[ImportedRow]:
        rows: list[ImportedRow] = []
        for match in CURRENT_ROW.finditer(text):
            purchase = parse_date(match.group("purchase"))
            booked = parse_date(match.group("booked"))
            amount = parse_amount(match.group("amount"))
            if not purchase or not booked or amount is None:
                continue
            description = re.sub(r"\s+", " ", match.group("body")).strip()
            if description and not description.upper().startswith(("IMPORTO", "TOTALE")):
                rows.append(ImportedRow(booked, description, amount, value_on=purchase, raw_data=match.group(0)))
        return rows

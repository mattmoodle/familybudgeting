from __future__ import annotations

import re
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date
from app.services.importers.pdf_utils import extract_pdf_text

# Newer monthly statement: DATE SIGN AMOUNT VALUE DESCRIPTION
NEW_ROW = re.compile(
    r"^\s*(?P<booked>\d{1,2}/\d{2}/\d{2})\s+(?P<sign>[DA])\s+(?P<amount>[\d.]+,\d{2})\s+(?P<value>\d{1,2}/\d{2}/\d{2})\s+(?P<body>.+)$",
    re.I,
)
# Older statement: DATE VALUE AMOUNT DESCRIPTION; debit/credit alignment may be lost in text extraction.
OLD_ROW = re.compile(
    r"^\s*(?P<booked>\d{1,2}/\d{2}/\d{2})\s+(?P<value>\d{1,2}/\d{2}/\d{2})\s+(?P<amount>[\d.]+,\d{2})\s+(?P<body>.+)$",
    re.I,
)


def _match(line: str):
    return NEW_ROW.match(line) or OLD_ROW.match(line)


class BperPdfImporter(StatementImporter):
    @staticmethod
    def matches(text: str) -> bool:
        upper = text.upper()
        return "BPMOIT22" in upper or ("RATA PRESTITO" in upper and "05387" in upper)

    def parse(self, path: Path) -> list[ImportedRow]:
        return self.parse_text(extract_pdf_text(path))

    def parse_text(self, text: str) -> list[ImportedRow]:
        lines = text.splitlines()
        rows: list[ImportedRow] = []
        i = 0
        while i < len(lines):
            line = " ".join(lines[i].split())
            m = _match(line)
            if not m:
                i += 1
                continue
            booked = parse_date(m.group("booked"))
            value = parse_date(m.group("value"))
            amount = parse_amount(m.group("amount"))
            if not booked or amount is None:
                i += 1
                continue
            sign = m.groupdict().get("sign", "").upper()
            body_parts = [m.group("body")]
            j = i + 1
            while j < len(lines):
                nxt = " ".join(lines[j].split())
                if _match(nxt):
                    break
                if nxt and not nxt.startswith(("FOGLIO", "Pagina", "A ")):
                    body_parts.append(nxt)
                j += 1
            body = " ".join(body_parts).strip()
            if sign == "D":
                amount = -abs(amount)
            elif sign == "A":
                amount = abs(amount)
            else:
                upper = body.upper()
                debit_hints = ("RATA PRESTITO", "PREL.", "PRELIEVO", "SPESE", "COMMISSION", "ADDEBIT")
                credit_hints = ("BONIFICO ISTANTANEO O/C:", "BONIFICO A VS FAVORE", "ACCREDITO")
                if any(h in upper for h in debit_hints):
                    amount = -abs(amount)
                elif any(h in upper for h in credit_hints):
                    amount = abs(amount)
                else:
                    i = max(i + 1, j)
                    continue
            rows.append(ImportedRow(booked, body, amount, value_on=value, raw_data=" ".join(body_parts)))
            i = max(i + 1, j)
        if not rows:
            raise ValueError("BPER statement recognized, but no unambiguous transaction rows were parsed")
        return rows

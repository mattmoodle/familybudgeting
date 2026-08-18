from __future__ import annotations

import re
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date
from app.services.importers.pdf_utils import extract_pdf_text

START = re.compile(r"^\s*(?P<date>\d{2}/\d{2}/\d{2})\s+(?P<desc>.+?)\s*$")
ID_RE = re.compile(r"\bID:\s*([A-Z0-9]+)", re.I)
TRIPLE_END = re.compile(r"(?P<gross>-?[\d.]+,\d{2})\s+(?P<fee>-?[\d.]+,\d{2})\s+(?P<net>-?[\d.]+,\d{2})\s*$")
CURRENCY_HEADING = re.compile(r"Cronologia transazioni\s*-\s*([A-Z]{3})", re.I)


class PaypalPdfImporter(StatementImporter):
    @staticmethod
    def matches(text: str) -> bool:
        return "CRONOLOGIA TRANSAZIONI" in text.upper() and "PAYPAL" in text.upper() and "LORDO" in text.upper()

    def parse(self, path: Path) -> list[ImportedRow]:
        return self.parse_text(extract_pdf_text(path))

    def parse_text(self, text: str) -> list[ImportedRow]:
        lines = [" ".join(x.split()) for x in text.splitlines()]
        rows: list[ImportedRow] = []
        currency = "EUR"
        i = 0
        while i < len(lines):
            heading = CURRENCY_HEADING.search(lines[i])
            if heading:
                currency = heading.group(1).upper()
                i += 1
                continue
            start = START.match(lines[i])
            if not start:
                i += 1
                continue
            booked = parse_date(start.group("date"))
            if not booked:
                i += 1
                continue
            block = [start.group("desc")]
            j = i + 1
            while j < len(lines):
                if START.match(lines[j]) or CURRENCY_HEADING.search(lines[j]):
                    break
                if lines[j].startswith("Codice conto commerciante") or lines[j].startswith("Pagina "):
                    j += 1
                    continue
                block.append(lines[j])
                if TRIPLE_END.search(lines[j]):
                    break
                j += 1
            combined = " ".join(x for x in block if x)
            amount_m = TRIPLE_END.search(combined)
            if amount_m and booked:
                net = parse_amount(amount_m.group("net"))
                if net is not None:
                    prefix = combined[:amount_m.start()].strip()
                    txid_m = ID_RE.search(prefix)
                    txid = txid_m.group(1) if txid_m else None
                    clean = ID_RE.sub("", prefix).strip(" -")
                    if txid:
                        clean = f"{clean} [PayPal ID {txid}]"
                    rows.append(ImportedRow(booked, clean, net, currency=currency, raw_data=combined))
            i = max(i + 1, j)
        if not rows:
            raise ValueError("PayPal statement recognized, but no transactions were parsed")
        return rows

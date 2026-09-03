from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount
from app.services.importers.pdf_utils import extract_pdf_text

MONTHS = {"gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,"lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12}
DATE_RE = re.compile(
    r"^\s*(\d{1,2})\s+(gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)\s+(\d{4})(?:\s+(?P<rest>.*))?$",
    re.I,
)
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Some PDF producers expose the euro glyph as one or more replacement characters.
# Accept it (or the normal glyph) while still requiring a money-shaped value.
AMOUNT_RE = re.compile(r"(?<![\d.,])-?[\d.]+,\d{2}\s*(?:€|\ufffd)+")


class SatispayPdfImporter(StatementImporter):
    @staticmethod
    def matches(text: str) -> bool:
        upper = text.upper()
        return "LISTA TRANSAZIONI" in upper and "SATISPAY" in upper and "DISPONIBILIT" in upper

    def parse(self, path: Path) -> list[ImportedRow]:
        return self.parse_text(extract_pdf_text(path))

    def parse_text(self, text: str) -> list[ImportedRow]:
        lines = [" ".join(x.split()) for x in text.splitlines()]
        rows: list[ImportedRow] = []
        i = 0
        while i < len(lines):
            dm = DATE_RE.match(lines[i])
            if not dm:
                i += 1
                continue
            booked = datetime(int(dm.group(3)), MONTHS[dm.group(2).lower()], int(dm.group(1))).date()
            block: list[str] = [dm.group("rest") or ""]
            j = i + 1
            while j < len(lines) and not DATE_RE.match(lines[j]):
                if lines[j].lower().startswith("pagina "):
                    break
                block.append(lines[j])
                j += 1
            combined = " ".join(x for x in block if x)
            uuid_m = UUID_RE.search(combined)
            amounts = AMOUNT_RE.findall(combined)
            if amounts:
                # In the Satispay report the first monetary amount is the transaction amount;
                # later values are balance fields.
                amount = parse_amount(amounts[0])
                if amount is not None:
                    description = combined
                    if uuid_m:
                        description = description[:uuid_m.start()].strip() + f" [Satispay ID {uuid_m.group(0)}]"
                    # Strip time and duplicated amount/balance tail while preserving type words.
                    description = re.sub(r"^\d{1,2}:\d{2}\s+", "", description)
                    first_amount_pos = AMOUNT_RE.search(description)
                    if first_amount_pos:
                        head = description[:first_amount_pos.start()].strip()
                        tail = description[first_amount_pos.end():]
                        # Keep semantic type labels, drop numeric balances/status noise.
                        tail = AMOUNT_RE.sub(" ", tail)
                        tail = re.sub(r"\bApprovato\b", " ", tail, flags=re.I)
                        tail = re.sub(r"\s+", " ", tail).strip(" -")
                        description = f"{head} {tail}".strip()
                    rows.append(ImportedRow(booked, description, amount, raw_data=combined))
            i = max(i + 1, j)
        if not rows:
            raise ValueError("Satispay statement recognized, but no transactions were parsed")
        return rows

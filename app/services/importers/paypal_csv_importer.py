from __future__ import annotations

import csv
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import parse_amount, parse_date


class PaypalCsvImporter(StatementImporter):
    """Parser for PayPal's Italian transaction-history CSV export."""

    REQUIRED_HEADERS = {"data", "descrizione", "valuta", "netto", "codice transazione"}

    @classmethod
    def matches(cls, path: Path) -> bool:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,")
            headers = next(csv.reader(text.splitlines(), dialect), [])
        except (csv.Error, StopIteration):
            return False
        normalized = {header.strip().lower() for header in headers}
        return cls.REQUIRED_HEADERS.issubset(normalized)

    def parse(self, path: Path) -> list[ImportedRow]:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,")
        reader = csv.DictReader(text.splitlines(), dialect=dialect)
        rows: list[ImportedRow] = []
        for source in reader:
            booked = parse_date(source.get("Data"))
            amount = parse_amount(source.get("Netto"))
            description = (source.get("Descrizione") or "").strip()
            transaction_id = (source.get("Codice transazione") or "").strip()
            if not booked or amount is None or not description:
                continue
            counterparty = (source.get("Nome") or "").strip()
            if counterparty and counterparty.casefold() not in description.casefold():
                description = f"{description} - {counterparty}"
            if transaction_id:
                description = f"{description} [PayPal ID {transaction_id}]"
            currency = (source.get("Valuta") or "EUR").strip().upper() or "EUR"
            rows.append(
                ImportedRow(
                    booked_on=booked,
                    description=description,
                    amount=amount,
                    currency=currency,
                    raw_data=str(source),
                )
            )
        if not rows:
            raise ValueError("PayPal CSV recognized, but no transaction rows were parsed")
        return rows

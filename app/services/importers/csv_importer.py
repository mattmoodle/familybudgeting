import csv
from pathlib import Path

from app.services.importers.base import ImportedRow, StatementImporter
from app.services.importers.common import choose_columns, parse_amount, parse_date


class CsvImporter(StatementImporter):
    def parse(self, path: Path) -> list[ImportedRow]:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
        rows = list(csv.reader(text.splitlines(), dialect))
        if not rows:
            return []
        headers = rows[0]
        date_col, value_col, desc_col, amount_col, credit_col = choose_columns(headers)
        if date_col is None or desc_col is None or amount_col is None:
            raise ValueError("Unable to identify date/description/amount columns in CSV")

        result: list[ImportedRow] = []
        for row in rows[1:]:
            if len(row) <= max(date_col, desc_col, amount_col):
                continue
            booked = parse_date(row[date_col])
            amount = parse_amount(row[amount_col])
            if credit_col is not None and credit_col < len(row):
                credit = parse_amount(row[credit_col])
                if credit not in (None, 0):
                    amount = abs(credit)
                elif amount is not None:
                    amount = -abs(amount)
            if booked is None or amount is None:
                continue
            value_on = parse_date(row[value_col]) if value_col is not None and value_col < len(row) else None
            result.append(ImportedRow(booked, row[desc_col].strip(), amount, value_on, raw_data=str(row)))
        return result

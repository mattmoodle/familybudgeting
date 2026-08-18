from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y")


def parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip().replace("€", "").replace("EUR", "").replace(" ", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def choose_columns(headers: list[str]) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    normalized = [str(h or "").strip().lower() for h in headers]

    def find(*needles: str) -> int | None:
        for i, header in enumerate(normalized):
            if any(n in header for n in needles):
                return i
        return None

    date_col = find("data oper", "data cont", "data", "date", "booked")
    value_col = find("valuta", "value date")
    description_col = find("descr", "causale", "details", "merchant", "narrative")
    amount_col = find("importo", "amount", "movimento")
    debit_col = find("addeb", "debit", "uscit")
    credit_col = find("accred", "credit", "entrat")
    if amount_col is None and debit_col is not None:
        amount_col = debit_col
    return date_col, value_col, description_col, amount_col, credit_col

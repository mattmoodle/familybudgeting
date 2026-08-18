from datetime import date
from decimal import Decimal

from app.services.normalization import normalize_description, source_uid


def test_normalize_description():
    assert normalize_description("  BOTTON D’Oro!  ") == "botton d oro"


def test_source_uid_is_stable():
    a = source_uid(1, date(2026, 1, 1), Decimal("-10.00"), "Shop")
    b = source_uid(1, date(2026, 1, 1), Decimal("-10.00"), "SHOP")
    assert a == b

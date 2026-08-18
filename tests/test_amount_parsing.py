from decimal import Decimal

from app.services.importers.common import parse_amount


def test_european_amount():
    assert parse_amount("1.234,56 EUR") == Decimal("1234.56")


def test_negative_amount():
    assert parse_amount("-82,40") == Decimal("-82.40")

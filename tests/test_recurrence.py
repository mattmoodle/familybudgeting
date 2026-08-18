from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import Account, Transaction
from app.services.recurrence import cost_structure, detect_recurring_patterns, forecast_recurring_expenses


def tx(account_id: int, d: date, desc: str, amount: str, category: str, merchant: str):
    return Transaction(
        account_id=account_id,
        booked_on=d,
        description=desc,
        normalized_description=desc.lower(),
        merchant=merchant,
        amount=Decimal(amount),
        source_uid=f"{account_id}-{d}-{desc}-{amount}",
        category=category,
        category_confidence=Decimal("0.95"),
    )


def test_recurring_detection_forecast_and_cost_structure():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Bank", account_type="bank")
        db.add(account)
        db.flush()
        db.add_all(
            [
                tx(account.id, date(2026, 5, 2), "Netflix", "-12.99", "Shopping", "netflix"),
                tx(account.id, date(2026, 6, 1), "Netflix", "-12.99", "Shopping", "netflix"),
                tx(account.id, date(2026, 7, 1), "Netflix", "-12.99", "Shopping", "netflix"),
                tx(account.id, date(2026, 8, 1), "Netflix", "-12.99", "Shopping", "netflix"),
                tx(account.id, date(2026, 5, 7), "Groceries", "-70.00", "Groceries", "conad"),
                tx(account.id, date(2026, 5, 14), "Groceries", "-95.00", "Groceries", "conad"),
                tx(account.id, date(2026, 5, 21), "Groceries", "-65.00", "Groceries", "conad"),
                tx(account.id, date(2026, 5, 28), "Groceries", "-105.00", "Groceries", "conad"),
                tx(account.id, date(2026, 8, 3), "One-off appliance", "-400.00", "Home", "appliance shop"),
            ]
        )
        db.commit()

        patterns = detect_recurring_patterns(db)
        netflix = next(p for p in patterns if p.merchant == "netflix")
        groceries = next(p for p in patterns if p.merchant == "conad")
        assert netflix.cadence == "monthly"
        assert netflix.cost_type == "fixed"
        assert groceries.cadence == "weekly"
        assert groceries.cost_type == "variable"

        forecast = forecast_recurring_expenses(db, horizon_days=40, as_of=date(2026, 8, 3))
        assert any(item.merchant == "netflix" and item.expected_on == date(2026, 9, 1) for item in forecast)
        assert any(item.merchant == "conad" for item in forecast)

        structure = cost_structure(db)
        assert structure["fixed"] == Decimal("51.96")
        assert structure["variable"] == Decimal("335.00")
        assert structure["occasional"] == Decimal("400.00")

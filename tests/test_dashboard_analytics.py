from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import Account, Transaction
from app.services.analytics import category_totals, dashboard_summary, monthly_cashflow, review_queue


def tx(account_id: int, d: date, desc: str, amount: str, category: str, confidence: str = "0.95"):
    return Transaction(
        account_id=account_id,
        booked_on=d,
        description=desc,
        normalized_description=desc.lower(),
        amount=Decimal(amount),
        source_uid=f"{account_id}-{d}-{desc}-{amount}",
        category=category,
        category_confidence=Decimal(confidence),
    )


def test_dashboard_filters_and_review_queue():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bank = Account(name="Bank", account_type="bank")
        card = Account(name="Card", account_type="card")
        db.add_all([bank, card])
        db.flush()
        db.add_all(
            [
                tx(bank.id, date(2026, 7, 1), "Salary", "3000", "Income"),
                tx(bank.id, date(2026, 7, 2), "Mortgage", "-1000", "Housing"),
                tx(card.id, date(2026, 7, 3), "Restaurant", "-80", "Restaurants"),
                tx(card.id, date(2026, 7, 4), "Mystery", "-20", "Uncategorized", "0.20"),
            ]
        )
        db.commit()

        summary = dashboard_summary(db)
        assert summary.income == Decimal("3000.00")
        assert summary.expenses == Decimal("1100.00")

        card_summary = dashboard_summary(db, account_id=card.id)
        assert card_summary.income == Decimal("0.00")
        assert card_summary.expenses == Decimal("100.00")

        restaurants = category_totals(db, category="Restaurants")
        assert restaurants == [{"category": "Restaurants", "amount": Decimal("80.00")}]

        cashflow = monthly_cashflow(db, account_id=bank.id)
        assert cashflow[0]["net"] == Decimal("2000.00")

        review = review_queue(db)
        assert [item.description for item in review] == ["Mystery"]

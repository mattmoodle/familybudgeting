from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import Account, Category, Transaction
from app.services.budgeting import budget_vs_actual, upsert_budget
from app.services.recurrence import (
    detect_recurring_patterns,
    forecast_recurring_expenses,
    supporting_transactions_for_recurrence,
    upsert_recurrence_override,
)


def tx(account_id: int, d: date, amount: str, category: str, merchant: str):
    return Transaction(
        account_id=account_id,
        booked_on=d,
        description=merchant,
        normalized_description=merchant,
        merchant=merchant,
        amount=Decimal(amount),
        source_uid=f"{account_id}-{d}-{merchant}-{amount}",
        category=category,
        category_confidence=Decimal("0.95"),
    )


def setup_monthly(db: Session):
    account = Account(name="Bank", account_type="bank")
    db.add(account)
    db.add(Category(name="Housing"))
    db.flush()
    db.add_all([
        tx(account.id, date(2026, 5, 26), "-1000", "Housing", "mortgage"),
        tx(account.id, date(2026, 6, 26), "-1000", "Housing", "mortgage"),
        tx(account.id, date(2026, 7, 26), "-1000", "Housing", "mortgage"),
    ])
    db.commit()
    return account


def test_confirmed_override_changes_amount_date_and_forecast():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        setup_monthly(db)
        pattern = detect_recurring_patterns(db)[0]
        upsert_recurrence_override(
            db, pattern.key, pattern.merchant, pattern.category, "confirmed",
            override_amount=Decimal("1025"), override_next_expected=date(2026, 8, 28), note="manual check",
        )
        managed = detect_recurring_patterns(db)[0]
        assert managed.management_status == "confirmed"
        assert managed.manual_override is True
        assert managed.average_amount == Decimal("1025")
        assert managed.next_expected == date(2026, 8, 28)
        forecast = forecast_recurring_expenses(db, horizon_days=30, as_of=date(2026, 8, 15))
        assert forecast[0].expected_on == date(2026, 8, 28)
        assert forecast[0].estimated_amount == Decimal("1025")


def test_display_name_changes_forecast_label_without_changing_original_merchant():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        setup_monthly(db)
        pattern = detect_recurring_patterns(db)[0]
        upsert_recurrence_override(
            db, pattern.key, pattern.merchant, pattern.category, "confirmed", display_name="Home loan"
        )
        managed = detect_recurring_patterns(db)[0]
        forecast = forecast_recurring_expenses(db, horizon_days=40, as_of=date(2026, 8, 1))

        assert managed.merchant == "mortgage"
        assert managed.display_name == "Home loan"
        assert forecast[0].merchant == "mortgage"
        assert forecast[0].display_name == "Home loan"
        evidence = supporting_transactions_for_recurrence(db, "Home loan", "Housing", pattern_key=forecast[0].key)
        assert len(evidence) == 3


def test_rejected_and_paused_patterns_do_not_create_future_expenses():
    for status in ("rejected", "paused", "ended"):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            setup_monthly(db)
            pattern = detect_recurring_patterns(db)[0]
            upsert_recurrence_override(db, pattern.key, pattern.merchant, pattern.category, status)
            forecast = forecast_recurring_expenses(db, horizon_days=60, as_of=date(2026, 8, 15))
            assert not forecast
            patterns = detect_recurring_patterns(db)
            if status == "rejected":
                assert patterns == []
            else:
                assert patterns[0].management_status == status


def test_budget_forecast_honours_manual_recurrence_override():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        setup_monthly(db)
        upsert_budget(db, "2026-08", "Housing", Decimal("1200"))
        pattern = detect_recurring_patterns(db)[0]
        upsert_recurrence_override(
            db, pattern.key, pattern.merchant, pattern.category, "confirmed",
            override_amount=Decimal("1100"), override_next_expected=date(2026, 8, 27),
        )
        report = budget_vs_actual(db, "2026-08", today=date(2026, 8, 15))
        housing = next(i for i in report["items"] if i["category"] == "Housing")
        assert housing["recurring_future"] == Decimal("1100.00")
        assert housing["projected"] == Decimal("1100.00")

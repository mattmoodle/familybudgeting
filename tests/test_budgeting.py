from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import Account, Category, Transaction
from app.services.budgeting import budget_vs_actual, copy_budgets, upsert_budget


def tx(account_id: int, d: date, amount: str, category: str, desc: str):
    return Transaction(
        account_id=account_id,
        booked_on=d,
        description=desc,
        normalized_description=desc.lower(),
        merchant=desc.lower(),
        amount=Decimal(amount),
        source_uid=f"{account_id}-{d}-{desc}-{amount}",
        category=category,
        category_confidence=Decimal("0.95"),
    )


def test_budget_vs_actual_projection_and_statuses():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Bank", account_type="bank")
        db.add(account)
        db.add_all([
            Category(name="Groceries", essential=True),
            Category(name="Restaurants", essential=False),
            Category(name="Travel", essential=False),
        ])
        db.flush()
        db.add_all([
            tx(account.id, date(2026, 8, 2), "-160", "Groceries", "Supermarket"),
            tx(account.id, date(2026, 8, 10), "-90", "Groceries", "Supermarket 2"),
            tx(account.id, date(2026, 8, 7), "-120", "Restaurants", "Dinner"),
            tx(account.id, date(2026, 8, 12), "-80", "Travel", "Train"),
        ])
        db.commit()

        upsert_budget(db, "2026-08", "Groceries", Decimal("500"))
        upsert_budget(db, "2026-08", "Restaurants", Decimal("150"))

        report = budget_vs_actual(db, "2026-08", today=date(2026, 8, 15))
        groceries = next(i for i in report["items"] if i["category"] == "Groceries")
        restaurants = next(i for i in report["items"] if i["category"] == "Restaurants")
        travel = next(i for i in report["items"] if i["category"] == "Travel")

        assert groceries["actual"] == Decimal("250.00")
        assert groceries["remaining"] == Decimal("250.00")
        assert groceries["projected"] == Decimal("516.67")
        assert groceries["status"] == "risk"
        assert restaurants["status"] == "risk"
        assert travel["status"] == "unbudgeted"
        assert report["total_budget"] == Decimal("650.00")
        assert report["total_actual"] == Decimal("450.00")


def test_copy_monthly_budgets_does_not_overwrite_existing_target():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Category(name="Groceries"), Category(name="Restaurants")])
        db.commit()
        upsert_budget(db, "2026-07", "Groceries", Decimal("450"))
        upsert_budget(db, "2026-07", "Restaurants", Decimal("120"))
        upsert_budget(db, "2026-08", "Groceries", Decimal("500"))

        copied = copy_budgets(db, "2026-07", "2026-08")
        report = budget_vs_actual(db, "2026-08", today=date(2026, 8, 31))
        by_category = {i["category"]: i["budget"] for i in report["items"]}

        assert copied == 1
        assert by_category["Groceries"] == Decimal("500.00")
        assert by_category["Restaurants"] == Decimal("120.00")


def test_hybrid_forecast_does_not_double_count_monthly_recurring_expense():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Bank", account_type="bank")
        db.add(account)
        db.add_all([Category(name="Subscriptions"), Category(name="Groceries")])
        db.flush()
        # Monthly recurring fixed expense already paid in August. Its next expected
        # occurrence is in September, so August run-rate must not extrapolate it.
        db.add_all([
            tx(account.id, date(2026, 6, 2), "-50", "Subscriptions", "Streaming Plus"),
            tx(account.id, date(2026, 7, 2), "-50", "Subscriptions", "Streaming Plus"),
            tx(account.id, date(2026, 8, 1), "-50", "Subscriptions", "Streaming Plus"),
            tx(account.id, date(2026, 8, 5), "-100", "Groceries", "Market A"),
            tx(account.id, date(2026, 8, 10), "-50", "Groceries", "Market B"),
        ])
        db.commit()
        upsert_budget(db, "2026-08", "Subscriptions", Decimal("60"))
        upsert_budget(db, "2026-08", "Groceries", Decimal("400"))

        report = budget_vs_actual(db, "2026-08", today=date(2026, 8, 15))
        subscriptions = next(i for i in report["items"] if i["category"] == "Subscriptions")
        groceries = next(i for i in report["items"] if i["category"] == "Groceries")

        assert subscriptions["recurring_actual"] == Decimal("50.00")
        assert subscriptions["recurring_future"] == Decimal("0.00")
        assert subscriptions["variable_remaining_projection"] == Decimal("0.00")
        assert subscriptions["projected"] == Decimal("50.00")
        assert groceries["projected"] == Decimal("310.00")


def test_hybrid_forecast_adds_future_recurring_expense_once():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Bank", account_type="bank")
        db.add(account)
        db.add(Category(name="Housing"))
        db.flush()
        # Monthly bill around the 25th; as of Aug 15 it is still expected this month.
        db.add_all([
            tx(account.id, date(2026, 5, 27), "-1000", "Housing", "Mortgage"),
            tx(account.id, date(2026, 6, 26), "-1000", "Housing", "Mortgage"),
            tx(account.id, date(2026, 7, 26), "-1000", "Housing", "Mortgage"),
        ])
        db.commit()
        upsert_budget(db, "2026-08", "Housing", Decimal("1100"))

        report = budget_vs_actual(db, "2026-08", today=date(2026, 8, 15))
        housing = next(i for i in report["items"] if i["category"] == "Housing")

        assert housing["actual"] == Decimal("0.00")
        assert housing["recurring_future"] == Decimal("1000.00")
        assert housing["recurring_future_count"] == 1
        assert housing["projected"] == Decimal("1000.00")
        assert report["total_recurring_future"] == Decimal("1000.00")

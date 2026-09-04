from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes import home, import_history, patch_transactions_bulk
from app.db.base import Base
from app.models.entities import Account, HumanCheckItem, ImportBatch, Transaction
from app.schemas.domain import TransactionBulkPatch
from app.services.analytics import category_totals, dashboard_summary, monthly_cashflow, review_queue, suspicious_queue


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
        db.flush()
        mortgage = db.scalar(select(Transaction).where(Transaction.description == "Mortgage"))
        mortgage.is_suspicious = True
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
        review[0].review_completed = True
        db.commit()
        assert review_queue(db) == []
        assert [item.description for item in suspicious_queue(db)] == ["Mortgage"]


def test_transactions_page_paginates_search_results_and_bulk_updates():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Transaction Test", account_type="bank")
        db.add(account)
        db.flush()
        transactions = [
            tx(account.id, date(2026, 8, 1), f"Example shop {index}", "-10", "Uncategorized")
            for index in range(25)
        ]
        db.add_all(transactions)
        db.commit()

        request = Request({"type": "http", "method": "GET", "path": "/transactions", "headers": [], "query_string": b"q=Example+shop&per_page=20&page=2"})
        response = home(request=request, q="Example shop", per_page="20", page="2", db=db)
        rendered = response.body.decode()
        assert "25 risultati · pagina 2 di 2" in rendered
        assert rendered.count('class="transaction-select"') == 5

        result = patch_transactions_bulk(
            TransactionBulkPatch(transaction_ids=[transactions[0].id, transactions[1].id], category="Shopping", is_suspicious=True),
            db,
        )
        assert result == {"updated": 2}
        updated = db.scalars(select(Transaction).where(Transaction.id.in_([transactions[0].id, transactions[1].id]))).all()
        assert {item.category for item in updated} == {"Shopping"}
        assert all(item.is_suspicious for item in updated)


def test_import_history_shows_statement_period_and_sorts_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Archive Bank", account_type="bank")
        db.add(account)
        db.flush()
        human_batch = ImportBatch(
            source_filename="june-human-check.pdf",
            stored_path="data/archive/june-human-check.pdf",
            file_hash="a" * 64,
            account_id=account.id,
            status="human_check",
            import_mode="human-check",
            created_at=datetime(2026, 9, 3, 12, 0),
        )
        standard_batch = ImportBatch(
            source_filename="december.csv",
            stored_path="data/archive/december.csv",
            file_hash="b" * 64,
            account_id=account.id,
            status="completed",
            import_mode="standard",
            created_at=datetime(2026, 9, 4, 12, 0),
        )
        db.add_all([human_batch, standard_batch])
        db.flush()
        db.add_all([
            HumanCheckItem(import_batch_id=human_batch.id, sequence=1, original_text="first", parsed_booked_on=date(2026, 6, 1), parsed_description="First", parsed_amount=Decimal("-5")),
            HumanCheckItem(import_batch_id=human_batch.id, sequence=2, original_text="last", parsed_booked_on=date(2026, 6, 30), parsed_description="Last", parsed_amount=Decimal("-10")),
            tx(account.id, date(2026, 12, 1), "Statement transaction", "-25", "Shopping"),
        ])
        db.flush()
        transaction = db.scalar(select(Transaction).where(Transaction.description == "Statement transaction"))
        transaction.import_batch_id = standard_batch.id
        db.commit()

        request = Request({"type": "http", "method": "GET", "path": "/imports", "headers": [], "query_string": b"import_sort=first_transaction_on&import_direction=asc"})
        response = import_history(request=request, import_sort="first_transaction_on", import_direction="asc", db=db)
        rendered = response.body.decode()
        assert "01/06/2026" in rendered
        assert "30/06/2026" in rendered
        assert rendered.index("june-human-check.pdf") < rendered.index("december.csv")

        filtered = import_history(request=request, import_q="december", db=db).body.decode()
        assert "december.csv" in filtered
        assert "june-human-check.pdf" not in filtered

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import human_check_decision
from app.core.config import settings
from app.db.base import Base
from app.models.entities import Account, HumanCheckDecision, HumanCheckItem, RecurrenceOverride, Transaction
from app.services.human_check import finalize_human_check
from app.services.import_service import import_statement
from app.schemas.domain import HumanCheckDecisionPatch
from app.services.classification import classify


def test_human_check_stages_before_ledger_and_finalizes(tmp_path):
    old_data_dir = settings.data_dir
    settings.data_dir = tmp_path / "data"
    try:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            account = Account(name="Test Bank", account_type="bank")
            db.add(account)
            db.commit()

            content = (
                "Data;Descrizione;Importo\n"
                "01/08/2026;CONAD ROMA;-45,20\n"
                "02/08/2026;BOTTON D ORO;-32,00\n"
            ).encode()
            batch, imported, skipped, duplicate = import_statement(
                db, account.id, "statement.csv", content, mode="human-check"
            )
            assert imported == 0
            assert skipped == 0
            assert duplicate is False
            assert db.scalars(select(Transaction)).all() == []

            items = db.scalars(select(HumanCheckItem).order_by(HumanCheckItem.sequence)).all()
            assert len(items) == 2
            assert items[0].parsed_category == "Groceries"
            assert items[1].parsed_category == "Restaurants"

            items[0].decision = HumanCheckDecision.ACCEPTED.value
            items[0].is_recurring = True
            items[0].recurrence_cadence = "monthly"
            items[1].decision = HumanCheckDecision.CORRECTED.value
            items[1].corrected_description = "Botton d'Oro restaurant"
            items[1].corrected_amount = Decimal("-30.00")
            items[1].corrected_booked_on = date(2026, 8, 2)
            items[1].corrected_category = "Restaurants"
            db.commit()

            imported, skipped = finalize_human_check(db, batch.id)
            assert imported == 2
            assert skipped == 0
            txs = db.scalars(select(Transaction).order_by(Transaction.booked_on)).all()
            assert txs[0].category_source == "human-approved"
            assert txs[1].category_source == "manual"
            assert txs[1].amount == Decimal("-30.00")
            recurrence = db.scalar(select(RecurrenceOverride).where(RecurrenceOverride.pattern_key == f"manual:human-check:{items[0].id}"))
            assert recurrence is not None
            assert recurrence.override_cadence == "monthly"
            assert recurrence.override_amount == Decimal("45.20")
    finally:
        settings.data_dir = old_data_dir


def test_human_check_accepts_direct_manual_edit_and_learns_category(tmp_path):
    old_data_dir = settings.data_dir
    settings.data_dir = tmp_path / "data"
    try:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            account = Account(name="Test Card", account_type="card")
            db.add(account)
            db.commit()
            batch, _, _, _ = import_statement(
                db, account.id, "statement.csv", b"Data;Descrizione;Importo\n01/08/2026;UNKNOWN PAYMENT;-8,50\n", mode="human-check"
            )
            item = db.scalar(select(HumanCheckItem))
            updated = human_check_decision(
                item.id,
                HumanCheckDecisionPatch(
                    decision="accepted", apply_manual_correction=True,
                    booked_on=date(2026, 8, 1), description="Pagamento Caffe Aurora Roma 123456",
                    amount=Decimal("-8.50"), category="Restaurants", is_suspicious=True,
                ),
                db,
            )
            assert updated.decision == HumanCheckDecision.CORRECTED.value
            imported, skipped = finalize_human_check(db, batch.id)
            assert (imported, skipped) == (1, 0)
            saved = db.scalar(select(Transaction))
            assert saved.category_source == "manual"
            assert saved.is_suspicious is True
            learned = classify("Carta POS Caffe Aurora Roma 654321", db)
            assert learned.category == "Restaurants"
            assert learned.source == "learned-local"
    finally:
        settings.data_dir = old_data_dir

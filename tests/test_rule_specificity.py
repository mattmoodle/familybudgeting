from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import Account, Rule, Transaction
from app.services.classification import classify, rule_pattern_for_transaction


def transaction(account_id: int, description: str, source_uid: str) -> Transaction:
    return Transaction(
        account_id=account_id,
        booked_on=date(2026, 8, 3),
        description=description,
        normalized_description=description.lower(),
        merchant="",
        amount=Decimal("-9.99"),
        source_uid=source_uid,
        category="Uncategorized",
    )


def test_conflicting_generic_rules_are_ignored_in_favor_of_specific_rule():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Rule(pattern="pagamento tramite pos pagamenti", category="Restaurants", priority=10),
            Rule(pattern="pagamento tramite pos pagamenti", category="Fuel", priority=10),
            Rule(pattern="audible", category="Subscriptions", priority=10),
        ])
        db.commit()

        result = classify("Pagamento tramite POS pagamenti Eurozona Audible IT", db)
        assert result.category == "Subscriptions"
        assert result.source == "rule"


def test_new_rule_pattern_prefers_distinctive_merchant_term_over_payment_boilerplate():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Bank", account_type="bank")
        db.add(account)
        db.flush()
        db.add_all([
            transaction(account.id, "Pagamento tramite POS pagamenti PAOLA Audible IT", "a"),
            transaction(account.id, "Pagamento tramite POS pagamenti PAOLA Supermercato Roma", "b"),
        ])
        db.commit()

        assert rule_pattern_for_transaction("Pagamento tramite POS pagamenti PAOLA Audible IT", db) == "audible"

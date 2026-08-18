from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import Account, Transaction
from app.services.classification import classify
from app.services.merchant import normalize_merchant


def test_local_classifier_learns_from_manual_correction():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="Card", account_type="card")
        db.add(account)
        db.flush()
        merchant = normalize_merchant("PAGAMENTO POS CAFFE AURORA ROMA 123456")
        db.add(
            Transaction(
                account_id=account.id,
                booked_on=date(2026, 8, 1),
                description="PAGAMENTO POS CAFFE AURORA ROMA 123456",
                normalized_description="pagamento pos caffe aurora roma 123456",
                merchant=merchant,
                amount=Decimal("-5.00"),
                source_uid="manual-example",
                category="Restaurants",
                category_confidence=Decimal("1.0"),
                category_source="manual",
            )
        )
        db.commit()

        result = classify("Carta POS Caffe Aurora Roma 998877", db)
        assert result.category == "Restaurants"
        assert result.source == "learned-local"
        assert result.confidence >= Decimal("0.89")

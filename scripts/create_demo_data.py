from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import date
from decimal import Decimal

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.entities import Account, Transaction
from app.services.classification import classify
from app.services.normalization import normalize_description, source_uid
from app.services.reconciliation import reconcile_transaction
from app.services.seed import seed_categories


def add_tx(db, account, d, desc, amount):
    amount = Decimal(amount)
    tx = Transaction(
        account_id=account.id,
        booked_on=d,
        description=desc,
        normalized_description=normalize_description(desc),
        amount=amount,
        source_uid=source_uid(account.id, d, amount, desc),
    )
    c = classify(desc, db)
    tx.category, tx.category_confidence, tx.category_source = c.category, c.confidence, c.source
    db.add(tx)
    db.flush()
    reconcile_transaction(db, tx)


Base.metadata.create_all(engine)
with SessionLocal() as db:
    seed_categories(db)
    bank = Account(name="Demo Current Account", account_type="bank")
    paypal = Account(name="Demo PayPal", account_type="paypal")
    db.add_all([bank, paypal]); db.flush()
    add_tx(db, bank, date(2026, 7, 1), "Stipendio", "3000.00")
    add_tx(db, bank, date(2026, 7, 2), "Rata mutuo", "-1000.00")
    add_tx(db, bank, date(2026, 7, 6), "EUROSPIN", "-82.40")
    add_tx(db, bank, date(2026, 7, 10), "Ricarica PayPal", "-120.00")
    add_tx(db, paypal, date(2026, 7, 10), "Top up from bank", "120.00")
    add_tx(db, paypal, date(2026, 7, 11), "CHEERZ", "-39.90")
    add_tx(db, bank, date(2026, 7, 18), "Botton d Oro", "-72.00")
    db.commit()
print("Demo database created.")

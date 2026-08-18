from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Account, AccountType, Transaction

TRANSFER_HINTS = (
    "paypal", "satispay", "giroconto", "bonifico interno", "ricarica", "top up",
    "carta di credito", "credit card", "visa", "mastercard", "amex",
    "salvadanaio", "deposito risparmi", "prelievo risparmi", "deposito investimento",
    "verso la banca", "dalla banca", "bonifico bancario sul conto paypal",
    "versamento generico con carta", "trasferimento avviato dall utente",
)


def mark_exact_duplicates(db: Session, tx: Transaction) -> None:
    lower = tx.booked_on - timedelta(days=settings.dedup_date_window_days)
    upper = tx.booked_on + timedelta(days=settings.dedup_date_window_days)
    candidate = db.scalar(
        select(Transaction)
        .where(
            Transaction.id != tx.id,
            Transaction.account_id == tx.account_id,
            Transaction.booked_on.between(lower, upper),
            Transaction.amount == tx.amount,
            Transaction.normalized_description == tx.normalized_description,
            Transaction.is_duplicate.is_(False),
        )
        .order_by(Transaction.id.asc())
    )
    if candidate:
        tx.is_duplicate = True
        tx.duplicate_of_id = candidate.id
        tx.excluded_from_analytics = True


def mark_internal_transfer(db: Session, tx: Transaction) -> None:
    if tx.is_duplicate:
        return
    lower = tx.booked_on - timedelta(days=settings.transfer_window_days)
    upper = tx.booked_on + timedelta(days=settings.transfer_window_days)
    opposite = db.scalar(
        select(Transaction)
        .where(
            Transaction.id != tx.id,
            Transaction.account_id != tx.account_id,
            Transaction.booked_on.between(lower, upper),
            Transaction.amount == -tx.amount,
            Transaction.is_duplicate.is_(False),
            Transaction.transfer_pair_id.is_(None),
        )
        .order_by(func.abs(func.julianday(Transaction.booked_on) - func.julianday(tx.booked_on)))
    )
    if opposite:
        tx.is_internal_transfer = True
        opposite.is_internal_transfer = True
        tx.transfer_pair_id = opposite.id
        opposite.transfer_pair_id = tx.id
        tx.excluded_from_analytics = True
        opposite.excluded_from_analytics = True
        return

    # Conservative one-sided fallback for wallet/card funding descriptions.
    account_type = db.scalar(select(Account.account_type).where(Account.id == tx.account_id))
    desc = tx.normalized_description
    is_wallet = account_type in {AccountType.PAYPAL.value, AccountType.SATISPAY.value}
    if any(hint in desc for hint in TRANSFER_HINTS):
        if (account_type == AccountType.BANK.value and tx.amount < 0) or is_wallet:
            tx.is_internal_transfer = True
            tx.excluded_from_analytics = True


def reconcile_transaction(db: Session, tx: Transaction) -> None:
    mark_exact_duplicates(db, tx)
    db.flush()
    mark_internal_transfer(db, tx)

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import HumanCheckDecision, HumanCheckItem, ImportBatch, ImportStatus, Transaction
from app.services.classification import classify
from app.services.merchant import normalize_merchant
from app.services.normalization import normalize_description, source_uid
from app.services.reconciliation import reconcile_transaction
from app.services.recurrence import advance_recurring_date, upsert_recurrence_override


def batch_progress(db: Session, batch_id: int) -> dict[str, int]:
    rows = db.execute(
        select(HumanCheckItem.decision, func.count(HumanCheckItem.id))
        .where(HumanCheckItem.import_batch_id == batch_id)
        .group_by(HumanCheckItem.decision)
    ).all()
    counts = {decision: count for decision, count in rows}
    total = sum(counts.values())
    return {
        "total": total,
        "pending": counts.get(HumanCheckDecision.PENDING.value, 0),
        "accepted": counts.get(HumanCheckDecision.ACCEPTED.value, 0),
        "rejected": counts.get(HumanCheckDecision.REJECTED.value, 0),
        "corrected": counts.get(HumanCheckDecision.CORRECTED.value, 0),
    }


def finalize_human_check(db: Session, batch_id: int) -> tuple[int, int]:
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.import_mode != "human-check":
        raise ValueError("Human-check batch not found")
    if batch.status == ImportStatus.COMPLETED.value:
        return batch.imported_rows, batch.skipped_rows

    items = db.scalars(
        select(HumanCheckItem)
        .where(HumanCheckItem.import_batch_id == batch_id)
        .order_by(HumanCheckItem.sequence)
    ).all()
    unresolved = [i for i in items if i.decision in {HumanCheckDecision.PENDING.value, HumanCheckDecision.REJECTED.value}]
    if unresolved:
        raise ValueError("Complete all human-check decisions and corrections before finalizing")

    imported = 0
    skipped = 0
    for item in items:
        booked_on = item.corrected_booked_on or item.parsed_booked_on
        description = item.corrected_description or item.parsed_description
        amount = item.corrected_amount if item.corrected_amount is not None else item.parsed_amount
        category = item.corrected_category or item.parsed_category
        merchant = normalize_merchant(description)
        uid = source_uid(batch.account_id, booked_on, amount, description)
        already_present = db.scalar(
            select(Transaction.id).where(Transaction.account_id == batch.account_id, Transaction.source_uid == uid)
        )
        if already_present:
            skipped += 1
            continue

        tx = Transaction(
            account_id=batch.account_id,
            import_batch_id=batch.id,
            booked_on=booked_on,
            value_on=item.parsed_value_on,
            description=description,
            normalized_description=normalize_description(description),
            merchant=merchant,
            amount=amount,
            currency=item.parsed_currency,
            source_uid=uid,
            category=category,
            category_confidence=Decimal("1.000") if item.decision == HumanCheckDecision.CORRECTED.value else item.parsed_confidence,
            category_source="manual" if item.decision == HumanCheckDecision.CORRECTED.value else "human-approved",
            is_suspicious=item.is_suspicious,
            manual_note=item.user_note,
            raw_data=json.dumps({"human_check_item": item.id, "original": item.original_text}, ensure_ascii=False),
        )
        db.add(tx)
        db.flush()
        reconcile_transaction(db, tx)
        if item.is_recurring and item.recurrence_cadence and amount < 0:
            upsert_recurrence_override(
                db, f"manual:human-check:{item.id}", merchant or description[:80], category, "confirmed",
                override_amount=abs(Decimal(str(amount))),
                override_next_expected=advance_recurring_date(booked_on, item.recurrence_cadence, {"weekly": 7, "biweekly": 14, "monthly": 30, "bimonthly": 60, "quarterly": 91, "semiannual": 182, "annual": 365}[item.recurrence_cadence]),
                override_cadence=item.recurrence_cadence, note="Creata durante Human-check", commit=False,
            )
        imported += 1

    batch.status = ImportStatus.COMPLETED.value
    batch.imported_rows = imported
    batch.skipped_rows = skipped
    db.commit()
    return imported, skipped

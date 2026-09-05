from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import calendar
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import RecurrenceAlias, RecurrenceOverride, Transaction
from app.services.merchant import normalize_merchant

ZERO = Decimal("0.00")


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance_recurring_date(value: date, cadence: str, interval_days: int) -> date:
    """Advance a recurrence without accumulating calendar drift."""
    month_steps = {
        "monthly": 1,
        "bimonthly": 2,
        "quarterly": 3,
        "semiannual": 6,
        "annual": 12,
    }
    if cadence in month_steps:
        return _add_months(value, month_steps[cadence])
    return value + timedelta(days=interval_days)


@dataclass(frozen=True)
class RecurringPattern:
    key: str
    merchant: str
    category: str
    cadence: str
    interval_days: int
    occurrences: int
    average_amount: Decimal
    amount_variation: Decimal
    cost_type: str
    confidence: Decimal
    last_seen: date
    next_expected: date
    management_status: str = "auto"
    manual_override: bool = False
    note: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class ForecastItem:
    key: str
    expected_on: date
    merchant: str
    display_name: str | None
    category: str
    estimated_amount: Decimal
    cadence: str
    cost_type: str
    confidence: Decimal


def _cadence(interval_days: float) -> tuple[str, int] | None:
    windows = (
        (5, 9, "weekly", 7),
        (12, 18, "biweekly", 14),
        (25, 35, "monthly", 30),
        (50, 70, "bimonthly", 60),
        (75, 105, "quarterly", 91),
        (165, 200, "semiannual", 182),
        (330, 400, "annual", 365),
    )
    for low, high, name, canonical in windows:
        if low <= interval_days <= high:
            return name, canonical
    return None


def _recurrence_aliases(db: Session) -> dict[tuple[str, str], str]:
    return {
        (row.source_merchant, row.category): row.canonical_merchant
        for row in db.scalars(select(RecurrenceAlias)).all()
    }


def _pattern_key(tx: Transaction, aliases: dict[tuple[str, str], str] | None = None) -> tuple[str, str]:
    merchant = (tx.merchant or normalize_merchant(tx.description)).strip()
    if not merchant:
        merchant = tx.normalized_description.strip()[:80]
    if aliases:
        merchant = aliases.get((merchant, tx.category), merchant)
    return merchant, tx.category


def _canonical_days_for_cadence(cadence: str) -> int:
    return {
        "weekly": 7, "biweekly": 14, "monthly": 30, "bimonthly": 60,
        "quarterly": 91, "semiannual": 182, "annual": 365,
    }[cadence]


def upsert_recurrence_override(
    db: Session, pattern_key: str, merchant: str, category: str, status: str,
    override_amount: Decimal | None = None, override_next_expected: date | None = None,
    override_cadence: str | None = None, note: str | None = None,
    display_name: str | None = None, commit: bool = True,
) -> RecurrenceOverride:
    if status not in {"confirmed", "rejected", "paused", "ended"}:
        raise ValueError("invalid recurrence status")
    if override_cadence and override_cadence not in {"weekly", "biweekly", "monthly", "bimonthly", "quarterly", "semiannual", "annual"}:
        raise ValueError("invalid recurrence cadence")
    row = db.scalar(select(RecurrenceOverride).where(RecurrenceOverride.pattern_key == pattern_key))
    if row is None:
        row = RecurrenceOverride(pattern_key=pattern_key, merchant=merchant, category=category)
        db.add(row)
    row.merchant = merchant
    row.category = category
    row.status = status
    row.override_amount = override_amount
    row.override_next_expected = override_next_expected
    row.override_cadence = override_cadence
    row.note = note
    if display_name is not None:
        row.display_name = display_name.strip() or None
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def delete_recurrence_override(db: Session, pattern_key: str, commit: bool = True) -> bool:
    row = db.scalar(select(RecurrenceOverride).where(RecurrenceOverride.pattern_key == pattern_key))
    if row is None:
        return False
    db.delete(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def _apply_overrides(db: Session, patterns: list[RecurringPattern], include_rejected: bool = False) -> list[RecurringPattern]:
    overrides = {o.pattern_key: o for o in db.scalars(select(RecurrenceOverride)).all()}
    aliases = _recurrence_aliases(db)
    result: list[RecurringPattern] = []
    for pattern in patterns:
        override = overrides.get(pattern.key)
        if override is None:
            result.append(pattern)
            continue
        if override.status == "rejected" and not include_rejected:
            continue
        cadence = override.override_cadence or pattern.cadence
        interval_days = _canonical_days_for_cadence(cadence)
        result.append(RecurringPattern(
            key=pattern.key, merchant=pattern.merchant, category=pattern.category, cadence=cadence,
            interval_days=interval_days, occurrences=pattern.occurrences,
            average_amount=(override.override_amount or pattern.average_amount),
            amount_variation=pattern.amount_variation, cost_type=pattern.cost_type,
            confidence=Decimal("1.000") if override.status == "confirmed" else pattern.confidence,
            last_seen=pattern.last_seen, next_expected=(override.override_next_expected or pattern.next_expected),
            management_status=override.status, manual_override=True, note=override.note,
            display_name=override.display_name,
        ))
    # Human-check and transaction edits can create a manual recurrence before
    # there are enough ledger rows for automatic detection.  If those merchants
    # were later merged, aggregate their manual overrides under the canonical
    # name too, rather than showing stale one-row groups.
    canonical_manual_groups: dict[tuple[str, str, str], list[RecurrenceOverride]] = defaultdict(list)
    detected_keys = {(pattern.merchant, pattern.category) for pattern in result}
    for override in overrides.values():
        if not override.pattern_key.startswith("manual:") or (override.status == "rejected" and not include_rejected):
            continue
        if not override.override_amount or not override.override_next_expected or not override.override_cadence:
            continue
        canonical_merchant = aliases.get((override.merchant, override.category), override.merchant)
        if canonical_merchant != override.merchant:
            # An automatically detected group already represents these rows.
            if (canonical_merchant, override.category) in detected_keys:
                continue
            canonical_manual_groups[(canonical_merchant, override.category, override.override_cadence)].append(override)
            continue
        result.append(RecurringPattern(
            key=override.pattern_key, merchant=override.merchant, category=override.category,
            cadence=override.override_cadence, interval_days=_canonical_days_for_cadence(override.override_cadence),
            occurrences=0, average_amount=override.override_amount, amount_variation=ZERO,
            cost_type="fixed", confidence=Decimal("1.000"), last_seen=override.override_next_expected,
            next_expected=override.override_next_expected, management_status=override.status,
            manual_override=True, note=override.note, display_name=override.display_name,
        ))

    for (merchant, category, cadence), group in canonical_manual_groups.items():
        amounts = [item.override_amount for item in group if item.override_amount is not None]
        next_dates = [item.override_next_expected for item in group if item.override_next_expected is not None]
        assert amounts and next_dates  # Guaranteed by the validation above.
        average_amount = (sum(amounts, ZERO) / Decimal(len(amounts))).quantize(Decimal("0.01"))
        # A confirmed item is enough to keep the manually-created group active.
        status = "confirmed" if any(item.status == "confirmed" for item in group) else group[0].status
        display_name = next((item.display_name for item in group if item.display_name), None)
        notes = [item.note for item in group if item.note]
        result.append(RecurringPattern(
            key=f"manual:alias:{merchant}|{category}", merchant=merchant, category=category,
            cadence=cadence, interval_days=_canonical_days_for_cadence(cadence),
            occurrences=len(group), average_amount=average_amount, amount_variation=ZERO,
            cost_type="fixed", confidence=Decimal("1.000"), last_seen=max(next_dates),
            next_expected=min(next_dates), management_status=status, manual_override=True,
            note=" · ".join(notes) or None, display_name=display_name,
        ))
    return sorted(result, key=lambda item: (item.next_expected, -item.confidence))


def detect_recurring_patterns(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    category: str | None = None,
    min_occurrences: int = 3,
) -> list[RecurringPattern]:
    filters = [
        Transaction.amount < 0,
        Transaction.is_duplicate.is_(False),
        Transaction.is_internal_transfer.is_(False),
        Transaction.excluded_from_analytics.is_(False),
    ]
    if start:
        filters.append(Transaction.booked_on >= start)
    if end:
        filters.append(Transaction.booked_on <= end)
    if account_id:
        filters.append(Transaction.account_id == account_id)
    if category:
        filters.append(Transaction.category == category)

    txs = db.scalars(select(Transaction).where(*filters).order_by(Transaction.booked_on)).all()
    aliases = _recurrence_aliases(db)
    grouped: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in txs:
        merchant, tx_category = _pattern_key(tx, aliases)
        if merchant and merchant.lower() not in {"unknown", "uncategorized"}:
            grouped[(merchant, tx_category)].append(tx)

    result: list[RecurringPattern] = []
    for (merchant, tx_category), items in grouped.items():
        if len(items) < min_occurrences:
            continue
        items.sort(key=lambda item: item.booked_on)
        intervals = [(b.booked_on - a.booked_on).days for a, b in zip(items, items[1:])]
        if not intervals:
            continue
        med_interval = float(median(intervals))
        cadence = _cadence(med_interval)
        if not cadence:
            continue
        cadence_name, canonical_days = cadence

        # Require temporal regularity; a single cluster followed by a long gap should not become a subscription.
        interval_deviation = sum(abs(i - med_interval) for i in intervals) / len(intervals)
        timing_score = max(0.0, 1.0 - interval_deviation / max(canonical_days, 1))
        if timing_score < 0.55:
            continue

        amounts = [abs(Decimal(str(item.amount))) for item in items]
        avg_amount = (sum(amounts, ZERO) / Decimal(len(amounts))).quantize(Decimal("0.01"))
        if avg_amount == 0:
            continue
        mean_abs_dev = sum((abs(a - avg_amount) for a in amounts), ZERO) / Decimal(len(amounts))
        variation = (mean_abs_dev / avg_amount).quantize(Decimal("0.001"))
        cost_type = "fixed" if variation <= Decimal("0.12") else "variable"

        occurrence_score = min(1.0, len(items) / 6)
        confidence = Decimal(str(round(0.55 * timing_score + 0.30 * occurrence_score + 0.15 * (1.0 - min(float(variation), 1.0)), 3)))
        confidence = min(Decimal("0.990"), max(Decimal("0.500"), confidence))
        last_seen = items[-1].booked_on
        next_expected = advance_recurring_date(last_seen, cadence_name, canonical_days)
        result.append(
            RecurringPattern(
                key=f"{merchant}|{tx_category}",
                merchant=merchant,
                category=tx_category,
                cadence=cadence_name,
                interval_days=canonical_days,
                occurrences=len(items),
                average_amount=avg_amount,
                amount_variation=variation,
                cost_type=cost_type,
                confidence=confidence,
                last_seen=last_seen,
                next_expected=next_expected,
            )
        )

    return sorted(_apply_overrides(db, result), key=lambda item: (item.next_expected, -item.confidence))


def forecast_recurring_expenses(
    db: Session,
    horizon_days: int = 60,
    as_of: date | None = None,
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    category: str | None = None,
) -> list[ForecastItem]:
    patterns = detect_recurring_patterns(db, start, end, account_id, category)
    if as_of is None:
        # Keep forecasts deterministic for imported historical datasets: anchor to latest ledger date.
        latest = db.scalar(select(Transaction.booked_on).order_by(Transaction.booked_on.desc()).limit(1))
        as_of = latest or date.today()
    horizon = as_of + timedelta(days=max(1, min(horizon_days, 366)))

    items: list[ForecastItem] = []
    for pattern in patterns:
        if pattern.management_status in {"paused", "ended"}:
            continue
        expected = pattern.next_expected
        while expected <= as_of:
            expected = advance_recurring_date(expected, pattern.cadence, pattern.interval_days)
        while expected <= horizon:
            items.append(
                ForecastItem(
                    key=pattern.key,
                    expected_on=expected,
                    merchant=pattern.merchant,
                    display_name=pattern.display_name,
                    category=pattern.category,
                    estimated_amount=pattern.average_amount,
                    cadence=pattern.cadence,
                    cost_type=pattern.cost_type,
                    confidence=pattern.confidence,
                )
            )
            expected = advance_recurring_date(expected, pattern.cadence, pattern.interval_days)
    return sorted(items, key=lambda item: (item.expected_on, item.merchant))


def supporting_transactions_for_recurrence(
    db: Session, merchant: str, category: str, pattern_key: str | None = None, limit: int = 5
) -> list[Transaction]:
    """Return recent local ledger rows that belong to a detected recurrence."""
    candidates = db.scalars(
        select(Transaction)
        .where(
            Transaction.amount < 0,
            Transaction.category == category,
            Transaction.is_duplicate.is_(False),
            Transaction.is_internal_transfer.is_(False),
            Transaction.excluded_from_analytics.is_(False),
        )
        .order_by(Transaction.booked_on.desc(), Transaction.id.desc())
    ).all()
    if pattern_key and pattern_key.startswith("manual:alias:"):
        original_merchant = pattern_key.removeprefix("manual:alias:").split("|", 1)[0]
    else:
        original_merchant = pattern_key.split("|", 1)[0] if pattern_key and "|" in pattern_key else merchant
    aliases = _recurrence_aliases(db)
    return [tx for tx in candidates if _pattern_key(tx, aliases)[0] == original_merchant][:max(1, min(limit, 10))]


def cost_structure(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    category: str | None = None,
) -> dict[str, Decimal | int]:
    filters = [
        Transaction.amount < 0,
        Transaction.is_duplicate.is_(False),
        Transaction.is_internal_transfer.is_(False),
        Transaction.excluded_from_analytics.is_(False),
    ]
    if start:
        filters.append(Transaction.booked_on >= start)
    if end:
        filters.append(Transaction.booked_on <= end)
    if account_id:
        filters.append(Transaction.account_id == account_id)
    if category:
        filters.append(Transaction.category == category)
    txs = db.scalars(select(Transaction).where(*filters)).all()

    patterns = detect_recurring_patterns(db, start, end, account_id, category)
    pattern_types = {(p.merchant, p.category): p.cost_type for p in patterns}
    totals = {"fixed": ZERO, "variable": ZERO, "occasional": ZERO}
    counts = {"fixed": 0, "variable": 0, "occasional": 0}
    for tx in txs:
        key = _pattern_key(tx)
        cost_type = pattern_types.get(key, "occasional")
        totals[cost_type] += abs(Decimal(str(tx.amount)))
        counts[cost_type] += 1

    total = sum(totals.values(), ZERO)
    return {
        "fixed": totals["fixed"].quantize(Decimal("0.01")),
        "variable": totals["variable"].quantize(Decimal("0.01")),
        "occasional": totals["occasional"].quantize(Decimal("0.01")),
        "total": total.quantize(Decimal("0.01")),
        "fixed_count": counts["fixed"],
        "variable_count": counts["variable"],
        "occasional_count": counts["occasional"],
    }

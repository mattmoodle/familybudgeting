from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Budget, Category, Transaction
from app.services.merchant import normalize_merchant
from app.services.recurrence import advance_recurring_date, detect_recurring_patterns

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def month_bounds(month: str) -> tuple[date, date]:
    try:
        year_s, month_s = month.split("-", 1)
        year, mon = int(year_s), int(month_s)
        if not 1 <= mon <= 12:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError("month must be in YYYY-MM format") from exc
    last = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last)


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _tx_pattern_key(tx: Transaction) -> tuple[str, str]:
    merchant = (tx.merchant or normalize_merchant(tx.description)).strip()
    if not merchant:
        merchant = tx.normalized_description.strip()[:80]
    return merchant, tx.category


def upsert_budget(db: Session, month: str, category: str, amount: Decimal) -> Budget:
    if amount < 0:
        raise ValueError("Budget amount cannot be negative")
    month_start, _ = month_bounds(month)
    if not db.scalar(select(Category.id).where(Category.name == category)):
        raise ValueError("Category not found")
    budget = db.scalar(select(Budget).where(Budget.month == month_start, Budget.category == category))
    if budget is None:
        budget = Budget(month=month_start, category=category, amount=amount)
        db.add(budget)
    else:
        budget.amount = amount
    db.commit()
    db.refresh(budget)
    return budget


def delete_budget(db: Session, month: str, category: str) -> bool:
    month_start, _ = month_bounds(month)
    budget = db.scalar(select(Budget).where(Budget.month == month_start, Budget.category == category))
    if budget is None:
        return False
    db.delete(budget)
    db.commit()
    return True


def copy_budgets(db: Session, source_month: str, target_month: str) -> int:
    source_start, _ = month_bounds(source_month)
    target_start, _ = month_bounds(target_month)
    rows = db.scalars(select(Budget).where(Budget.month == source_start)).all()
    count = 0
    for row in rows:
        existing = db.scalar(select(Budget).where(Budget.month == target_start, Budget.category == row.category))
        if existing is None:
            db.add(Budget(month=target_start, category=row.category, amount=row.amount))
            count += 1
    db.commit()
    return count


def budget_vs_actual(db: Session, month: str, today: date | None = None) -> dict:
    """Return actuals plus an explainable hybrid end-of-month forecast.

    Forecast = actual spend to the cutoff + remaining non-recurring run-rate +
    explicitly expected recurring expenses. Recurring spend already observed is
    removed from the run-rate before extrapolation, preventing subscriptions or
    fixed bills from being counted twice.
    """
    start, end = month_bounds(month)
    today = today or date.today()
    budgets = db.scalars(select(Budget).where(Budget.month == start).order_by(Budget.category)).all()

    days_in_month = end.day
    if today < start:
        cutoff = start - timedelta(days=1)
        elapsed_days = 0
    elif today > end:
        cutoff = end
        elapsed_days = days_in_month
    else:
        cutoff = today
        elapsed_days = today.day

    tx_filters = [
        Transaction.booked_on >= start,
        Transaction.booked_on <= cutoff,
        Transaction.amount < 0,
        Transaction.excluded_from_analytics.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.is_internal_transfer.is_(False),
    ]
    month_txs = db.scalars(select(Transaction).where(*tx_filters).order_by(Transaction.booked_on)).all() if elapsed_days else []

    actual_by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for tx in month_txs:
        actual_by_category[tx.category] += abs(_money(tx.amount))
    actual_by_category = {k: _money(v) for k, v in actual_by_category.items()}

    # Patterns are inferred only from information available up to the cutoff: no look-ahead.
    patterns = detect_recurring_patterns(db, end=cutoff) if elapsed_days else []
    pattern_by_key = {(p.merchant, p.category): p for p in patterns}

    recurring_actual_by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for tx in month_txs:
        if _tx_pattern_key(tx) in pattern_by_key:
            recurring_actual_by_category[tx.category] += abs(_money(tx.amount))

    recurring_future_by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    recurring_future_count_by_category: dict[str, int] = defaultdict(int)
    if start <= today <= end:
        for pattern in patterns:
            if pattern.management_status in {"paused", "ended"}:
                continue
            expected = pattern.next_expected
            while expected <= cutoff:
                expected = advance_recurring_date(expected, pattern.cadence, pattern.interval_days)
            while expected <= end:
                recurring_future_by_category[pattern.category] += _money(pattern.average_amount)
                recurring_future_count_by_category[pattern.category] += 1
                expected = advance_recurring_date(expected, pattern.cadence, pattern.interval_days)

    categories = sorted(
        set(actual_by_category)
        | {b.category for b in budgets}
        | set(recurring_future_by_category)
    )
    budget_by_category = {b.category: _money(b.amount) for b in budgets}

    items = []
    for category in categories:
        budget = budget_by_category.get(category, ZERO)
        actual = actual_by_category.get(category, ZERO)
        recurring_actual = _money(recurring_actual_by_category.get(category, ZERO))
        variable_actual = _money(max(ZERO, actual - recurring_actual))
        recurring_future = _money(recurring_future_by_category.get(category, ZERO))

        remaining = (budget - actual).quantize(CENT)
        used_pct = ((actual / budget) * 100).quantize(Decimal("0.1")) if budget > 0 else None

        if elapsed_days == 0:
            variable_projected = ZERO
            projected = recurring_future
        elif elapsed_days >= days_in_month:
            variable_projected = variable_actual
            recurring_future = ZERO
            projected = actual
        else:
            variable_projected = (variable_actual / Decimal(elapsed_days) * Decimal(days_in_month)).quantize(CENT)
            projected = (recurring_actual + variable_projected + recurring_future).quantize(CENT)

        variable_remaining_projection = max(ZERO, variable_projected - variable_actual).quantize(CENT)
        projected_delta = (budget - projected).quantize(CENT)
        projected_pct = ((projected / budget) * 100).quantize(Decimal("0.1")) if budget > 0 else None

        if budget <= 0:
            status = "unbudgeted" if actual > 0 or projected > 0 else "none"
        elif actual > budget:
            status = "over"
        elif projected > budget:
            status = "risk"
        elif used_pct is not None and used_pct >= Decimal("85"):
            status = "warning"
        else:
            status = "on_track"

        items.append({
            "category": category,
            "budget": budget,
            "actual": actual,
            "remaining": remaining,
            "used_pct": used_pct,
            "projected": projected,
            "projected_delta": projected_delta,
            "projected_pct": projected_pct,
            "status": status,
            # Explainability fields for the hybrid forecast.
            "recurring_actual": recurring_actual,
            "variable_actual": variable_actual,
            "variable_projected": variable_projected,
            "variable_remaining_projection": variable_remaining_projection,
            "recurring_future": recurring_future,
            "recurring_future_count": recurring_future_count_by_category.get(category, 0),
        })

    total_budget = sum((x["budget"] for x in items), ZERO).quantize(CENT)
    total_actual = sum((x["actual"] for x in items), ZERO).quantize(CENT)
    total_projected = sum((x["projected"] for x in items), ZERO).quantize(CENT)
    total_recurring_future = sum((x["recurring_future"] for x in items), ZERO).quantize(CENT)
    total_variable_remaining = sum((x["variable_remaining_projection"] for x in items), ZERO).quantize(CENT)
    return {
        "month": month,
        "month_start": start,
        "month_end": end,
        "as_of": cutoff if elapsed_days else None,
        "elapsed_days": elapsed_days,
        "days_in_month": days_in_month,
        "forecast_method": "hybrid_recurring_plus_run_rate",
        "total_budget": total_budget,
        "total_actual": total_actual,
        "total_remaining": (total_budget - total_actual).quantize(CENT),
        "total_projected": total_projected,
        "total_recurring_future": total_recurring_future,
        "total_variable_remaining_projection": total_variable_remaining,
        "projected_delta": (total_budget - total_projected).quantize(CENT),
        "items": items,
    }

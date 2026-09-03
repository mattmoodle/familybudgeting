from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.entities import Transaction
from app.schemas.domain import DashboardSummary, MonthlyCategoryStat, SavingSuggestion

ZERO = Decimal("0.00")


def _base_filters(start: date | None, end: date | None, account_id: int | None = None, category: str | None = None):
    filters = [Transaction.excluded_from_analytics.is_(False), Transaction.is_duplicate.is_(False)]
    if start:
        filters.append(Transaction.booked_on >= start)
    if end:
        filters.append(Transaction.booked_on <= end)
    if account_id:
        filters.append(Transaction.account_id == account_id)
    if category:
        filters.append(Transaction.category == category)
    return filters


def dashboard_summary(db: Session, start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None) -> DashboardSummary:
    filters = _base_filters(start, end, account_id, category)
    income = db.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(*filters, Transaction.amount > 0)) or ZERO
    expenses_abs = db.scalar(select(func.coalesce(func.sum(-Transaction.amount), 0)).where(*filters, Transaction.amount < 0)) or ZERO
    transfer_total = db.scalar(
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(Transaction.is_internal_transfer.is_(True))
    ) or ZERO
    duplicates = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.is_duplicate.is_(True))) or 0
    income = Decimal(str(income)).quantize(Decimal("0.01"))
    expenses = Decimal(str(expenses_abs)).quantize(Decimal("0.01"))
    net = income - expenses
    savings_rate = ((net / income) * 100).quantize(Decimal("0.1")) if income > 0 else None
    return DashboardSummary(
        period_start=start,
        period_end=end,
        income=income,
        expenses=expenses,
        net_cashflow=net,
        savings_rate=savings_rate,
        excluded_internal_transfers=Decimal(str(transfer_total)).quantize(Decimal("0.01")),
        duplicate_rows=int(duplicates),
        generated_at=datetime.utcnow(),
    )


def monthly_category_stats(db: Session, start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None) -> list[MonthlyCategoryStat]:
    filters = _base_filters(start, end, account_id, category)
    rows = db.execute(
        select(
            func.strftime("%Y-%m", Transaction.booked_on).label("month"),
            Transaction.category,
            func.sum(-Transaction.amount).label("amount"),
        )
        .where(*filters, Transaction.amount < 0)
        .group_by("month", Transaction.category)
        .order_by("month", func.sum(-Transaction.amount).desc())
    ).all()
    return [MonthlyCategoryStat(month=m, category=c, amount=Decimal(str(a)).quantize(Decimal("0.01"))) for m, c, a in rows]


def category_totals(db: Session, start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None) -> list[dict]:
    filters = _base_filters(start, end, account_id, category)
    rows = db.execute(
        select(Transaction.category, func.sum(-Transaction.amount).label("amount"))
        .where(*filters, Transaction.amount < 0)
        .group_by(Transaction.category)
        .order_by(func.sum(-Transaction.amount).desc())
    ).all()
    return [{"category": category, "amount": Decimal(str(amount)).quantize(Decimal("0.01"))} for category, amount in rows]


def monthly_cashflow(db: Session, start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None) -> list[dict]:
    filters = _base_filters(start, end, account_id, category)
    rows = db.execute(
        select(
            func.strftime("%Y-%m", Transaction.booked_on).label("month"),
            func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0)).label("income"),
            func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)).label("expenses"),
        )
        .where(*filters)
        .group_by("month")
        .order_by("month")
    ).all()
    result = []
    for month, income, expenses in rows:
        inc = Decimal(str(income or 0)).quantize(Decimal("0.01"))
        exp = Decimal(str(expenses or 0)).quantize(Decimal("0.01"))
        result.append({"month": month, "income": inc, "expenses": exp, "net": inc - exp})
    return result


def review_queue(db: Session, limit: int = 30) -> list[Transaction]:
    return db.scalars(
        select(Transaction)
        .where(
            Transaction.is_duplicate.is_(False),
            Transaction.is_internal_transfer.is_(False),
            Transaction.excluded_from_analytics.is_(False),
            ((Transaction.category == "Uncategorized") | (Transaction.category_confidence < Decimal("0.75"))),
        )
        .order_by(Transaction.booked_on.desc(), Transaction.id.desc())
        .limit(limit)
    ).all()


def suspicious_queue(db: Session, limit: int = 30) -> list[Transaction]:
    """Flagged movements stay in analytics; this queue is only a review reminder."""
    return db.scalars(
        select(Transaction)
        .where(Transaction.is_suspicious.is_(True))
        .order_by(Transaction.booked_on.desc(), Transaction.id.desc())
        .limit(limit)
    ).all()


def saving_suggestions(db: Session, start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None) -> list[SavingSuggestion]:
    stats = monthly_category_stats(db, start, end, account_id, category)
    by_category: dict[str, list[Decimal]] = defaultdict(list)
    for item in stats:
        by_category[item.category].append(item.amount)

    discretionary = {"Restaurants", "Shopping", "Travel", "Photos & Prints", "Electronics"}
    suggestions: list[SavingSuggestion] = []
    for category, amounts in by_category.items():
        if category not in discretionary or not amounts:
            continue
        avg = sum(amounts, ZERO) / Decimal(len(amounts))
        saving = (avg * Decimal("0.15")).quantize(Decimal("0.01"))
        if saving < Decimal("15"):
            continue
        suggestions.append(
            SavingSuggestion(
                title=f"Riduci {category} del 15%",
                explanation=(
                    f"La spesa media mensile osservata è circa €{avg.quantize(Decimal('0.01'))}. "
                    "Un tetto del 15% lascerebbe invariata la categoria ma migliorerebbe il margine mensile."
                ),
                estimated_monthly_saving=saving,
                confidence="medium",
            )
        )
    return sorted(suggestions, key=lambda x: x.estimated_monthly_saving, reverse=True)[:5]

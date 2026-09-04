from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.session import engine, get_db
from app.models.entities import (
    Account,
    Category,
    HumanCheckDecision,
    HumanCheckItem,
    ImportBatch,
    RecurrenceAlias,
    RecurrenceOverride,
    Rule,
    Transaction,
)
from app.schemas.domain import (
    AccountCreate,
    AccountRead,
    CategoryCreate,
    CategoryRead,
    BudgetCopyRequest,
    BudgetRead,
    BudgetUpsert,
    CostStructureRead,
    DashboardSummary,
    ForecastItemRead,
    HumanCheckCorrection,
    HumanCheckDecisionPatch,
    HumanCheckItemRead,
    ImportResult,
    MonthlyCategoryStat,
    RecurrenceOverrideRead,
    RecurrenceOverrideUpsert,
    RecurrenceAliasUpsert,
    RecurringPatternRead,
    RuleCreate,
    RuleRead,
    RuleUpdate,
    SavingSuggestion,
    TransactionPatch,
    TransactionBulkPatch,
    TransactionRead,
)
from app.services.analytics import (
    category_totals,
    dashboard_summary,
    monthly_cashflow,
    monthly_category_stats,
    review_queue,
    suspicious_queue,
    saving_suggestions,
)
from app.services.backup_export import (
    create_backup,
    export_csv,
    export_xlsx,
    restore_backup,
    validate_backup,
)
from app.services.budgeting import budget_vs_actual, copy_budgets, delete_budget, upsert_budget
from app.services.human_check import batch_progress, finalize_human_check
from app.services.import_service import delete_import_batch, import_statement
from app.services.normalization import normalize_description
from app.services.recurrence import (
    advance_recurring_date,
    cost_structure,
    delete_recurrence_override,
    detect_recurring_patterns,
    forecast_recurring_expenses,
    supporting_transactions_for_recurrence,
    upsert_recurrence_override,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "Date must use YYYY-MM-DD") from exc


def optional_int(value: str | None, label: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(422, f"{label} must be an integer") from exc


@router.get("/", response_class=HTMLResponse)
@router.get("/budget", response_class=HTMLResponse)
@router.get("/transactions", response_class=HTMLResponse)
@router.get("/review", response_class=HTMLResponse)
@router.get("/recurrences", response_class=HTMLResponse)
@router.get("/insights", response_class=HTMLResponse)
def home(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    account_id: str | None = None,
    category: str | None = None,
    q: str | None = None,
    amount_type: str | None = None,
    transaction_state: str | None = None,
    recurrence_q: str | None = None,
    recurrence_category: str | None = None,
    recurrence_cadence: str | None = None,
    recurrence_type: str | None = None,
    recurrence_status: str | None = None,
    recurrence_sort: str | None = None,
    recurrence_direction: str | None = None,
    page: str | None = None,
    per_page: str | None = None,
    budget_month: str | None = None,
    db: Session = Depends(get_db),
):
    active_page = {
        "/": "overview",
        "/budget": "budget",
        "/transactions": "transactions",
        "/review": "review",
        "/recurrences": "recurrences",
        "/insights": "insights",
    }.get(request.url.path, "overview")
    start_date, end_date = optional_date(start), optional_date(end)
    selected_account_id = optional_int(account_id, "account_id")
    selected_page = optional_int(page, "page") or 1
    selected_per_page = optional_int(per_page, "per_page") or 50
    if selected_page < 1:
        raise HTTPException(422, "page must be positive")
    if selected_per_page not in {20, 50, 100}:
        raise HTTPException(422, "per_page must be 20, 50, or 100")
    summary = dashboard_summary(db, start_date, end_date, selected_account_id, category)
    accounts = db.scalars(select(Account).where(Account.active.is_(True)).order_by(Account.name)).all()
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    selected_budget_month = budget_month or date.today().strftime("%Y-%m")
    try:
        budget_report = budget_vs_actual(db, selected_budget_month)
    except ValueError:
        selected_budget_month = date.today().strftime("%Y-%m")
        budget_report = budget_vs_actual(db, selected_budget_month)

    tx_stmt = select(Transaction)
    if start_date:
        tx_stmt = tx_stmt.where(Transaction.booked_on >= start_date)
    if end_date:
        tx_stmt = tx_stmt.where(Transaction.booked_on <= end_date)
    if selected_account_id:
        tx_stmt = tx_stmt.where(Transaction.account_id == selected_account_id)
    if category:
        tx_stmt = tx_stmt.where(Transaction.category == category)
    search_term = (q or "").strip()
    if search_term:
        pattern = f"%{search_term}%"
        tx_stmt = tx_stmt.where(or_(Transaction.description.ilike(pattern), Transaction.merchant.ilike(pattern), Transaction.manual_note.ilike(pattern)))
    if amount_type == "expenses":
        tx_stmt = tx_stmt.where(Transaction.amount < 0)
    elif amount_type == "income":
        tx_stmt = tx_stmt.where(Transaction.amount > 0)
    if transaction_state == "counted":
        tx_stmt = tx_stmt.where(Transaction.excluded_from_analytics.is_(False), Transaction.is_internal_transfer.is_(False), Transaction.is_duplicate.is_(False))
    elif transaction_state == "excluded":
        tx_stmt = tx_stmt.where(Transaction.excluded_from_analytics.is_(True))
    elif transaction_state == "internal":
        tx_stmt = tx_stmt.where(Transaction.is_internal_transfer.is_(True))
    elif transaction_state == "duplicate":
        tx_stmt = tx_stmt.where(Transaction.is_duplicate.is_(True))
    elif transaction_state == "suspicious":
        tx_stmt = tx_stmt.where(Transaction.is_suspicious.is_(True))
    transaction_total = db.scalar(select(func.count()).select_from(tx_stmt.subquery())) or 0
    total_pages = max(1, (transaction_total + selected_per_page - 1) // selected_per_page)
    if selected_page > total_pages:
        selected_page = total_pages
    recent = db.scalars(
        tx_stmt.order_by(Transaction.booked_on.desc(), Transaction.id.desc())
        .offset((selected_page - 1) * selected_per_page)
        .limit(selected_per_page)
    ).all()
    recurring_all = detect_recurring_patterns(db, start_date, end_date, selected_account_id, category)
    recurrence_search = (recurrence_q or "").strip().casefold()
    recurrence_display = [
        item for item in recurring_all
        if (not recurrence_search or recurrence_search in item.merchant.casefold())
        and (not recurrence_category or item.category == recurrence_category)
        and (not recurrence_cadence or item.cadence == recurrence_cadence)
        and (not recurrence_type or item.cost_type == recurrence_type)
        and (not recurrence_status or item.management_status == recurrence_status)
    ]
    recurrence_sort_key = recurrence_sort if recurrence_sort in {"merchant", "category", "cadence", "cost_type", "average_amount", "occurrences", "next_expected", "management_status"} else "next_expected"
    recurrence_descending = recurrence_direction == "desc"
    recurrence_display.sort(key=lambda item: getattr(item, recurrence_sort_key), reverse=recurrence_descending)
    transaction_recurrences: dict[int, RecurrenceOverride] = {}
    for override in db.scalars(select(RecurrenceOverride).where(RecurrenceOverride.pattern_key.like("manual:transaction:%"))).all():
        suffix = override.pattern_key.rsplit(":", 1)[-1]
        if suffix.isdigit():
            transaction_recurrences[int(suffix)] = override

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "summary": summary,
            "active_page": active_page,
            "accounts": accounts,
            "categories": categories,
            "recent": recent,
            "transaction_recurrences": transaction_recurrences,
            "review": review_queue(db),
            "suspicious": suspicious_queue(db),
            "suggestions": saving_suggestions(db, start_date, end_date, selected_account_id, category),
            "category_totals": [{"category": x["category"], "amount": float(x["amount"])} for x in category_totals(db, start_date, end_date, selected_account_id, category)],
            "cashflow": [{"month": x["month"], "income": float(x["income"]), "expenses": float(x["expenses"]), "net": float(x["net"])} for x in monthly_cashflow(db, start_date, end_date, selected_account_id, category)],
            "filters": {"start": start_date, "end": end_date, "account_id": selected_account_id, "category": category, "q": search_term, "amount_type": amount_type or "", "transaction_state": transaction_state or "", "per_page": selected_per_page},
            "pagination": {"page": selected_page, "per_page": selected_per_page, "total": transaction_total, "total_pages": total_pages},
            "recurring": recurrence_display,
            "recurrence_filters": {"q": recurrence_q, "category": recurrence_category or "", "cadence": recurrence_cadence or "", "type": recurrence_type or "", "status": recurrence_status or "", "sort": recurrence_sort_key, "direction": "desc" if recurrence_descending else "asc"},
            "forecast": forecast_recurring_expenses(db, 60, start=start_date, end=end_date, account_id=selected_account_id, category=category)[:12],
            "cost_structure": cost_structure(db, start_date, end_date, selected_account_id, category),
            "budget_report": budget_report,
            "budget_month": selected_budget_month,
        },
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "offline-local"}


@router.get("/imports", response_class=HTMLResponse)
def import_history(
    request: Request,
    import_q: str | None = None,
    import_account_id: str | None = None,
    import_mode: str | None = None,
    import_status: str | None = None,
    imported_from: str | None = None,
    imported_to: str | None = None,
    import_sort: str | None = None,
    import_direction: str | None = None,
    db: Session = Depends(get_db),
):
    """Render the local archive with source-period ranges for each import batch."""
    selected_account_id = optional_int(import_account_id, "import_account_id")
    imported_start = optional_date(imported_from)
    imported_end = optional_date(imported_to)
    query = select(ImportBatch)
    search = (import_q or "").strip()
    if search:
        query = query.where(ImportBatch.source_filename.ilike(f"%{search}%"))
    if selected_account_id is not None:
        query = query.where(ImportBatch.account_id == selected_account_id)
    if import_mode in {"standard", "human-check"}:
        query = query.where(ImportBatch.import_mode == import_mode)
    if import_status:
        query = query.where(ImportBatch.status == import_status)

    batches = db.scalars(query).all()
    if imported_start or imported_end:
        batches = [
            batch for batch in batches
            if (not imported_start or batch.created_at.date() >= imported_start)
            and (not imported_end or batch.created_at.date() <= imported_end)
        ]
    batch_ids = [batch.id for batch in batches]
    transaction_ranges = {}
    human_check_ranges = {}
    if batch_ids:
        transaction_ranges = {
            batch_id: (first_on, last_on)
            for batch_id, first_on, last_on in db.execute(
                select(Transaction.import_batch_id, func.min(Transaction.booked_on), func.max(Transaction.booked_on))
                .where(Transaction.import_batch_id.in_(batch_ids))
                .group_by(Transaction.import_batch_id)
            )
        }
        human_check_ranges = {
            batch_id: (first_on, last_on)
            for batch_id, first_on, last_on in db.execute(
                select(
                    HumanCheckItem.import_batch_id,
                    func.min(func.coalesce(HumanCheckItem.corrected_booked_on, HumanCheckItem.parsed_booked_on)),
                    func.max(func.coalesce(HumanCheckItem.corrected_booked_on, HumanCheckItem.parsed_booked_on)),
                )
                .where(HumanCheckItem.import_batch_id.in_(batch_ids))
                .group_by(HumanCheckItem.import_batch_id)
            )
        }

    accounts = {account.id: account.name for account in db.scalars(select(Account)).all()}
    rows = []
    for batch in batches:
        # Human-check preserves staging rows, including corrected dates, so it represents
        # the complete original statement even while its final ledger is still incomplete.
        first_on, last_on = human_check_ranges.get(batch.id, transaction_ranges.get(batch.id, (None, None)))
        rows.append({
            "id": batch.id,
            "source_filename": batch.source_filename,
            "account_id": batch.account_id,
            "account_name": accounts.get(batch.account_id, "Non disponibile"),
            "created_at": batch.created_at,
            "import_mode": batch.import_mode,
            "status": batch.status,
            "imported_rows": batch.imported_rows,
            "skipped_rows": batch.skipped_rows,
            "first_transaction_on": first_on,
            "last_transaction_on": last_on,
        })
    valid_sort_fields = {"source_filename", "account_name", "created_at", "import_mode", "status", "first_transaction_on", "last_transaction_on", "imported_rows", "skipped_rows"}
    selected_sort = import_sort if import_sort in valid_sort_fields else "created_at"
    descending = import_direction != "asc"
    present_rows = [row for row in rows if row[selected_sort] is not None]
    empty_rows = [row for row in rows if row[selected_sort] is None]
    present_rows.sort(
        key=lambda row: row[selected_sort].casefold() if isinstance(row[selected_sort], str) else row[selected_sort],
        reverse=descending,
    )
    return templates.TemplateResponse(
        request=request,
        name="imports.html",
        context={
            "batches": present_rows + empty_rows,
            "accounts": accounts,
            "import_filters": {
                "q": import_q or "",
                "account_id": selected_account_id,
                "mode": import_mode or "",
                "status": import_status or "",
                "from": imported_start.isoformat() if imported_start else "",
                "to": imported_end.isoformat() if imported_end else "",
                "sort": selected_sort,
                "direction": "desc" if descending else "asc",
            },
        },
    )


@router.get("/rules", response_class=HTMLResponse)
def rules_page(
    request: Request,
    rules_q: str | None = None,
    rules_category: str | None = None,
    rules_status: str | None = None,
    rules_source: str | None = None,
    rules_sort: str | None = None,
    rules_direction: str | None = None,
    db: Session = Depends(get_db),
):
    """Show all locally learned classification rules for manual fine-tuning."""
    rules = db.scalars(select(Rule)).all()
    search = (rules_q or "").strip().casefold()
    rules = [
        rule for rule in rules
        if (not search or search in rule.pattern.casefold() or search in rule.category.casefold())
        and (not rules_category or rule.category == rules_category)
        and (rules_status != "active" or rule.active)
        and (rules_status != "inactive" or not rule.active)
        and (rules_source != "learned" or rule.created_from_manual_correction)
        and (rules_source != "manual" or not rule.created_from_manual_correction)
    ]
    valid_sort_fields = {"pattern", "category", "priority", "active", "created_from_manual_correction", "created_at"}
    selected_sort = rules_sort if rules_sort in valid_sort_fields else "priority"
    descending = rules_direction == "desc" if rules_sort in valid_sort_fields else False
    rules.sort(
        key=lambda rule: getattr(rule, selected_sort).casefold() if isinstance(getattr(rule, selected_sort), str) else getattr(rule, selected_sort),
        reverse=descending,
    )
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="rules.html",
        context={
            "rules": rules,
            "categories": categories,
            "rule_filters": {
                "q": rules_q or "", "category": rules_category or "", "status": rules_status or "",
                "source": rules_source or "", "sort": selected_sort, "direction": "desc" if descending else "asc",
            },
        },
    )


@router.post("/api/app/restart")
def restart_app() -> dict[str, str]:
    """Trigger Uvicorn's local reload watcher without changing source contents."""
    (Path(__file__).resolve().parents[1] / "main.py").touch()
    return {"status": "restarting"}


@router.post("/api/accounts", response_model=AccountRead)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Account).where(Account.name == payload.name)):
        raise HTTPException(409, "Account already exists")
    account = Account(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/api/accounts", response_model=list[AccountRead])
def list_accounts(db: Session = Depends(get_db)):
    return db.scalars(select(Account).order_by(Account.name)).all()


@router.post("/api/categories", response_model=CategoryRead)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    name = " ".join(payload.name.split())
    if db.scalar(select(Category).where(Category.name == name)):
        raise HTTPException(409, "Category already exists")
    category = Category(name=name, parent_name=payload.parent_name, essential=payload.essential)
    db.add(category); db.commit(); db.refresh(category)
    return category


@router.get("/api/categories", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)):
    return db.scalars(select(Category).order_by(Category.name)).all()


def _rule_text(value: str) -> str:
    return " ".join(value.split())


@router.get("/api/rules", response_model=list[RuleRead])
def list_rules(db: Session = Depends(get_db)):
    return db.scalars(select(Rule).order_by(Rule.priority, Rule.pattern, Rule.id)).all()


@router.post("/api/rules", response_model=RuleRead)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    pattern, category = _rule_text(payload.pattern), _rule_text(payload.category)
    existing = db.scalar(select(Rule).where(Rule.pattern == pattern, Rule.category == category))
    if existing:
        raise HTTPException(409, "A rule with this pattern and category already exists")
    rule = Rule(pattern=pattern, category=category, priority=payload.priority, active=payload.active, created_from_manual_correction=False)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/api/rules/{rule_id}", response_model=RuleRead)
def update_rule(rule_id: int, payload: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    pattern = _rule_text(payload.pattern) if payload.pattern is not None else rule.pattern
    category = _rule_text(payload.category) if payload.category is not None else rule.category
    duplicate = db.scalar(select(Rule).where(Rule.pattern == pattern, Rule.category == category, Rule.id != rule.id))
    if duplicate:
        raise HTTPException(409, "A rule with this pattern and category already exists")
    rule.pattern, rule.category = pattern, category
    if payload.priority is not None:
        rule.priority = payload.priority
    if payload.active is not None:
        rule.active = payload.active
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    db.commit()
    return {"deleted": True, "rule_id": rule_id}


@router.post("/api/imports", response_model=ImportResult)
async def upload_statement(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("standard"),
    db: Session = Depends(get_db),
):
    if not db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    content = await file.read()
    try:
        batch, imported, skipped, duplicate_file = import_statement(db, account_id, file.filename or "statement", content, mode=mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return ImportResult(batch_id=batch.id, imported=imported, skipped=skipped, duplicate_file=duplicate_file, import_mode=batch.import_mode, review_url=f"/human-check/{batch.id}" if batch.import_mode == "human-check" and batch.status != "completed" else None)


@router.delete("/api/imports/{batch_id}")
def delete_import(batch_id: int, db: Session = Depends(get_db)):
    try:
        transactions, staging_items = delete_import_batch(db, batch_id)
    except ValueError as exc:
        raise HTTPException(404 if str(exc) == "Import not found" else 422, str(exc)) from exc
    return {"batch_id": batch_id, "deleted_transactions": transactions, "deleted_staging_items": staging_items}


@router.get("/api/transactions", response_model=list[TransactionRead])
def list_transactions(
    category: str | None = None,
    account_id: int | None = None,
    include_excluded: bool = False,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    stmt = select(Transaction)
    if category:
        stmt = stmt.where(Transaction.category == category)
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if not include_excluded:
        stmt = stmt.where(Transaction.excluded_from_analytics.is_(False))
    return db.scalars(stmt.order_by(Transaction.booked_on.desc(), Transaction.id.desc()).limit(limit)).all()


@router.patch("/api/transactions/{transaction_id}", response_model=TransactionRead)
def patch_transaction(transaction_id: int, payload: TransactionPatch, db: Session = Depends(get_db)):
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if payload.category is not None:
        tx.category = payload.category
        tx.category_confidence = 1
        tx.category_source = "manual"
        if payload.create_rule and payload.category != "Uncategorized":
            pattern = normalize_description(tx.description)
            pattern = " ".join(pattern.split()[:4])
            if pattern and not db.scalar(select(Rule).where(Rule.pattern == pattern, Rule.category == payload.category)):
                db.add(Rule(pattern=pattern, category=payload.category, priority=10, created_from_manual_correction=True))
    if payload.excluded_from_analytics is not None:
        tx.excluded_from_analytics = payload.excluded_from_analytics
    if payload.manual_note is not None:
        tx.manual_note = payload.manual_note
    if payload.is_suspicious is not None:
        tx.is_suspicious = payload.is_suspicious
    if payload.review_completed is not None:
        tx.review_completed = payload.review_completed
    if payload.is_recurring is not None:
        pattern_key = f"manual:transaction:{tx.id}"
        if payload.is_recurring:
            if tx.amount >= 0:
                raise HTTPException(422, "Only expenses can be marked as recurring")
            cadence = payload.recurrence_cadence or "monthly"
            interval_days = {"weekly": 7, "biweekly": 14, "monthly": 30, "bimonthly": 60, "quarterly": 91, "semiannual": 182, "annual": 365}[cadence]
            merchant = tx.merchant or normalize_description(tx.description)[:180] or tx.description[:180]
            upsert_recurrence_override(
                db, pattern_key, merchant, tx.category, "confirmed",
                override_amount=abs(tx.amount),
                override_next_expected=advance_recurring_date(tx.booked_on, cadence, interval_days),
                override_cadence=cadence,
                note="Impostata dalla modifica della transazione",
                commit=False,
            )
        else:
            delete_recurrence_override(db, pattern_key, commit=False)
    db.commit()
    db.refresh(tx)
    return tx


@router.patch("/api/transactions/bulk-update")
def patch_transactions_bulk(payload: TransactionBulkPatch, db: Session = Depends(get_db)):
    values: dict[str, object] = {}
    if payload.category is not None:
        values.update(category=payload.category, category_confidence=1, category_source="manual")
    if payload.excluded_from_analytics is not None:
        values["excluded_from_analytics"] = payload.excluded_from_analytics
    if payload.is_suspicious is not None:
        values["is_suspicious"] = payload.is_suspicious
    if not values:
        raise HTTPException(422, "Choose a bulk action")
    result = db.execute(update(Transaction).where(Transaction.id.in_(payload.transaction_ids)).values(**values))
    db.commit()
    return {"updated": result.rowcount or 0}


@router.get("/api/recurrences/supporting-transactions")
def recurrence_supporting_transactions(
    merchant: str = Query(min_length=1, max_length=180),
    category: str = Query(min_length=1, max_length=80),
    pattern_key: str | None = Query(default=None, max_length=300),
    db: Session = Depends(get_db),
):
    transactions = supporting_transactions_for_recurrence(db, merchant, category, pattern_key=pattern_key)
    return {
        "transactions": [
            {
                "id": tx.id,
                "booked_on": tx.booked_on.isoformat(),
                "description": tx.description,
                "amount": float(tx.amount),
                "account": tx.account.name,
            }
            for tx in transactions
        ]
    }


@router.get("/human-check/{batch_id}", response_class=HTMLResponse)
def human_check_page(batch_id: int, request: Request, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.import_mode != "human-check":
        raise HTTPException(404, "Human-check batch not found")
    account = db.get(Account, batch.account_id)
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    items = db.scalars(
        select(HumanCheckItem).where(HumanCheckItem.import_batch_id == batch_id).order_by(HumanCheckItem.sequence)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="human_check.html",
        context={"batch": batch, "account": account, "items": items, "categories": categories, "progress": batch_progress(db, batch_id)},
    )


@router.get("/api/human-check/{batch_id}/items", response_model=list[HumanCheckItemRead])
def human_check_items(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.import_mode != "human-check":
        raise HTTPException(404, "Human-check batch not found")
    return db.scalars(select(HumanCheckItem).where(HumanCheckItem.import_batch_id == batch_id).order_by(HumanCheckItem.sequence)).all()


@router.get("/api/human-check/{batch_id}/progress")
def human_check_progress(batch_id: int, db: Session = Depends(get_db)):
    if not db.get(ImportBatch, batch_id):
        raise HTTPException(404, "Batch not found")
    return batch_progress(db, batch_id)


@router.patch("/api/human-check/items/{item_id}/decision", response_model=HumanCheckItemRead)
def human_check_decision(item_id: int, payload: HumanCheckDecisionPatch, db: Session = Depends(get_db)):
    item = db.get(HumanCheckItem, item_id)
    if not item:
        raise HTTPException(404, "Human-check item not found")
    if payload.decision not in {HumanCheckDecision.ACCEPTED.value, HumanCheckDecision.REJECTED.value}:
        raise HTTPException(422, "Decision must be accepted or rejected")
    if payload.apply_manual_correction:
        if not all((payload.booked_on, payload.description, payload.amount is not None, payload.category)):
            raise HTTPException(422, "Manual confirmation requires date, description, amount and category")
        item.corrected_booked_on = payload.booked_on
        item.corrected_description = payload.description
        item.corrected_amount = payload.amount
        item.corrected_category = payload.category
        item.decision = HumanCheckDecision.CORRECTED.value
    else:
        item.decision = payload.decision
    item.is_recurring = payload.is_recurring
    item.recurrence_cadence = payload.recurrence_cadence if payload.is_recurring else None
    item.is_suspicious = payload.is_suspicious
    db.commit()
    db.refresh(item)
    return item


@router.patch("/api/human-check/items/{item_id}/correction", response_model=HumanCheckItemRead)
def human_check_correction(item_id: int, payload: HumanCheckCorrection, db: Session = Depends(get_db)):
    item = db.get(HumanCheckItem, item_id)
    if not item:
        raise HTTPException(404, "Human-check item not found")
    item.corrected_booked_on = payload.booked_on
    item.corrected_description = payload.description
    item.corrected_amount = payload.amount
    item.corrected_category = payload.category
    item.user_note = payload.note
    item.is_recurring = payload.is_recurring
    item.recurrence_cadence = payload.recurrence_cadence if payload.is_recurring else None
    item.is_suspicious = payload.is_suspicious
    item.decision = HumanCheckDecision.CORRECTED.value
    db.commit()
    db.refresh(item)
    return item


@router.post("/api/human-check/{batch_id}/finalize")
def human_check_finalize(batch_id: int, db: Session = Depends(get_db)):
    try:
        imported, skipped = finalize_human_check(db, batch_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"batch_id": batch_id, "imported": imported, "skipped": skipped, "status": "completed"}


@router.put("/api/budgets", response_model=BudgetRead)
def set_budget(payload: BudgetUpsert, db: Session = Depends(get_db)):
    try:
        return upsert_budget(db, payload.month, payload.category, payload.amount)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/api/budgets/{month}/{category}")
def remove_budget(month: str, category: str, db: Session = Depends(get_db)):
    try:
        deleted = delete_budget(db, month, category)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Budget not found")
    return {"deleted": True}


@router.post("/api/budgets/copy")
def duplicate_budgets(payload: BudgetCopyRequest, db: Session = Depends(get_db)):
    try:
        copied = copy_budgets(db, payload.source_month, payload.target_month)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"copied": copied, "source_month": payload.source_month, "target_month": payload.target_month}


@router.get("/api/analytics/budget")
def budget_report(month: str, db: Session = Depends(get_db)):
    try:
        return budget_vs_actual(db, month)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/recurrences/overrides", response_model=list[RecurrenceOverrideRead])
def recurrence_overrides(db: Session = Depends(get_db)):
    return db.scalars(select(RecurrenceOverride).order_by(RecurrenceOverride.merchant, RecurrenceOverride.category)).all()


@router.put("/api/recurrences/override", response_model=RecurrenceOverrideRead)
def set_recurrence_override(payload: RecurrenceOverrideUpsert, db: Session = Depends(get_db)):
    try:
        return upsert_recurrence_override(db, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/api/recurrences/override")
def reset_recurrence_override(pattern_key: str, db: Session = Depends(get_db)):
    if not delete_recurrence_override(db, pattern_key):
        raise HTTPException(404, "Recurrence override not found")
    return {"deleted": True, "pattern_key": pattern_key}


@router.put("/api/recurrence-aliases")
def group_recurrence_aliases(payload: RecurrenceAliasUpsert, db: Session = Depends(get_db)):
    """Map merchant variants to one locally chosen recurrence group name."""
    category = " ".join(payload.category.split())
    canonical = " ".join(payload.canonical_merchant.split())
    sources = {" ".join(value.split()) for value in payload.source_merchants if value.strip()}
    if not canonical or not sources:
        raise HTTPException(422, "Choose a group name and at least one merchant variant")
    for source in sources:
        alias = db.scalar(select(RecurrenceAlias).where(RecurrenceAlias.source_merchant == source, RecurrenceAlias.category == category))
        if alias is None:
            alias = RecurrenceAlias(source_merchant=source, category=category, canonical_merchant=canonical)
            db.add(alias)
        else:
            alias.canonical_merchant = canonical
    db.commit()
    return {"grouped": len(sources), "category": category, "canonical_merchant": canonical}


@router.get("/api/analytics/recurring", response_model=list[RecurringPatternRead])
def recurring_patterns(
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    return detect_recurring_patterns(db, start, end, account_id, category)


@router.get("/api/analytics/forecast", response_model=list[ForecastItemRead])
def recurring_forecast(
    days: int = Query(default=60, ge=1, le=366),
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    return forecast_recurring_expenses(db, days, start=start, end=end, account_id=account_id, category=category)


@router.get("/api/analytics/cost-structure", response_model=CostStructureRead)
def analytics_cost_structure(
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    return cost_structure(db, start, end, account_id, category)


@router.get("/api/analytics/summary", response_model=DashboardSummary)
def summary(start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None, db: Session = Depends(get_db)):
    return dashboard_summary(db, start, end, account_id, category)


@router.get("/api/analytics/by-category", response_model=list[MonthlyCategoryStat])
def by_category(start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None, db: Session = Depends(get_db)):
    return monthly_category_stats(db, start, end, account_id, category)


@router.get("/api/analytics/suggestions", response_model=list[SavingSuggestion])
def suggestions(start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None, db: Session = Depends(get_db)):
    return saving_suggestions(db, start, end, account_id, category)


@router.get("/api/analytics/cashflow")
def cashflow(start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None, db: Session = Depends(get_db)):
    return monthly_cashflow(db, start, end, account_id, category)


@router.get("/api/analytics/category-totals")
def categories_total(start: date | None = None, end: date | None = None, account_id: int | None = None, category: str | None = None, db: Session = Depends(get_db)):
    return category_totals(db, start, end, account_id, category)


@router.post("/api/backup")
def backup_database():
    try:
        path = create_backup()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"filename": path.name, "path": str(path), "validated": True}


@router.post("/api/backup/validate")
async def validate_backup_upload(file: UploadFile = File(...)):
    with NamedTemporaryFile(suffix=".db", delete=False) as temporary:
        temporary.write(await file.read()); path = Path(temporary.name)
    try:
        return validate_backup(path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("/api/backup/restore")
async def restore_backup_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    with NamedTemporaryFile(suffix=".db", delete=False) as temporary:
        temporary.write(await file.read()); path = Path(temporary.name)
    try:
        # Release this request's SQLite handle before an atomic Windows file replacement.
        db.close()
        engine.dispose()
        safety_snapshot = restore_backup(path)
        return {"restored": True, "safety_backup": safety_snapshot.name}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.get("/api/export/csv")
def download_csv(db: Session = Depends(get_db)):
    path = export_csv(db)
    return FileResponse(path, filename=path.name, media_type="text/csv")


@router.get("/api/export/xlsx")
def download_xlsx(db: Session = Depends(get_db)):
    path = export_xlsx(db)
    return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

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
    RecurringPatternRead,
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
            "recurring": detect_recurring_patterns(db, start_date, end_date, selected_account_id, category),
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
def import_history(request: Request, db: Session = Depends(get_db)):
    batches = db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())).all()
    accounts = {account.id: account.name for account in db.scalars(select(Account)).all()}
    return templates.TemplateResponse(request=request, name="imports.html", context={"batches": batches, "accounts": accounts})


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
    db: Session = Depends(get_db),
):
    transactions = supporting_transactions_for_recurrence(db, merchant, category)
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

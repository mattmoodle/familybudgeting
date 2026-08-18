from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Account, Budget, Category, RecurrenceOverride, Rule, Transaction


def _database_path() -> Path:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix) or settings.database_url.endswith(":memory:"):
        raise ValueError("Backups are available only for a local SQLite file database")
    return Path(settings.database_url.removeprefix(prefix)).resolve()


def _validate_sqlite(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Backup file is missing or empty")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"accounts", "transactions", "categories"}.issubset(tables):
        raise ValueError("Backup is not compatible with Family Budget Offline")


def create_backup() -> Path:
    source = _database_path()
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    target = settings.backups_dir / f"family-budget-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)
    _validate_sqlite(target)
    return target


def validate_backup(path: Path) -> dict[str, bool | int]:
    _validate_sqlite(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return {"valid": True, "transactions": db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]}


def restore_backup(backup_path: Path) -> Path:
    """Validate first, snapshot the current ledger, then atomically replace it."""
    _validate_sqlite(backup_path)
    safety_snapshot = create_backup()
    destination = _database_path()
    replacement = destination.with_suffix(".restore-pending")
    try:
        with sqlite3.connect(backup_path) as source, sqlite3.connect(replacement) as target:
            source.backup(target)
        _validate_sqlite(replacement)
        os.replace(replacement, destination)
    except Exception:
        replacement.unlink(missing_ok=True)
        raise
    return safety_snapshot


def export_csv(db: Session) -> Path:
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.exports_dir / f"transactions-{datetime.now():%Y%m%d-%H%M%S}.csv"
    rows = db.scalars(select(Transaction).order_by(Transaction.booked_on, Transaction.id)).all()
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["id", "date", "account_id", "description", "merchant", "amount", "currency", "category", "excluded"])
        writer.writerows([[x.id, x.booked_on, x.account_id, x.description, x.merchant, x.amount, x.currency, x.category, x.excluded_from_analytics] for x in rows])
    return path


def export_xlsx(db: Session) -> Path:
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.exports_dir / f"family-budget-export-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    workbook = Workbook(); workbook.remove(workbook.active)
    for title, model, columns in [
        ("Transactions", Transaction, ["id", "booked_on", "account_id", "description", "merchant", "amount", "currency", "category"]),
        ("Accounts", Account, ["id", "name", "account_type", "currency", "active"]),
        ("Categories", Category, ["id", "name", "parent_name", "essential"]),
        ("Budgets", Budget, ["id", "month", "category", "amount"]),
        ("Rules", Rule, ["id", "pattern", "category", "priority", "active"]),
        ("Recurrence overrides", RecurrenceOverride, ["id", "pattern_key", "merchant", "category", "status", "override_amount", "override_next_expected"]),
    ]:
        sheet = workbook.create_sheet(title); sheet.append(columns)
        for row in db.scalars(select(model)).all(): sheet.append([getattr(row, field) for field in columns])
    workbook.save(path)
    return path

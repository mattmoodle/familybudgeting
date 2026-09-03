from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_schema_compatibility(engine: Engine) -> None:
    """Small idempotent SQLite compatibility migrations for local upgrades.

    The project intentionally keeps deployment lightweight for now. Once schema evolution
    becomes broader, these migrations can be replaced by Alembic without changing the
    service/domain layers.
    """
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements: list[str] = []

    if "import_batches" in tables:
        columns = {c["name"] for c in inspector.get_columns("import_batches")}
        if "import_mode" not in columns:
            statements.append(
                "ALTER TABLE import_batches ADD COLUMN import_mode VARCHAR(30) NOT NULL DEFAULT 'standard'"
            )

    if "transactions" in tables:
        columns = {c["name"] for c in inspector.get_columns("transactions")}
        if "merchant" not in columns:
            statements.append(
                "ALTER TABLE transactions ADD COLUMN merchant VARCHAR(180) NOT NULL DEFAULT ''"
            )
        if "is_suspicious" not in columns:
            statements.append("ALTER TABLE transactions ADD COLUMN is_suspicious BOOLEAN NOT NULL DEFAULT 0")
        statements.append(
            "CREATE INDEX IF NOT EXISTS ix_transactions_merchant ON transactions (merchant)"
        )

    if "human_check_items" in tables:
        columns = {c["name"] for c in inspector.get_columns("human_check_items")}
        if "is_recurring" not in columns:
            statements.append("ALTER TABLE human_check_items ADD COLUMN is_recurring BOOLEAN NOT NULL DEFAULT 0")
        if "recurrence_cadence" not in columns:
            statements.append("ALTER TABLE human_check_items ADD COLUMN recurrence_cadence VARCHAR(30)")
        if "is_suspicious" not in columns:
            statements.append("ALTER TABLE human_check_items ADD COLUMN is_suspicious BOOLEAN NOT NULL DEFAULT 0")

    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))

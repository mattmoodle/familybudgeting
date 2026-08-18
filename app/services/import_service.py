from __future__ import annotations

import json
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import HumanCheckItem, ImportBatch, ImportStatus, Transaction
from app.services.classification import classify
from app.services.importers import get_importer
from app.services.normalization import file_sha256, normalize_description, source_uid
from app.services.reconciliation import reconcile_transaction


def import_statement(
    db: Session,
    account_id: int,
    filename: str,
    content: bytes,
    mode: str = "standard",
) -> tuple[ImportBatch, int, int, bool]:
    if mode not in {"standard", "human-check"}:
        raise ValueError("Unsupported import mode")

    digest = file_sha256(content)
    existing = db.scalar(select(ImportBatch).where(ImportBatch.file_hash == digest))
    if existing:
        return existing, 0, 0, True

    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    temp_path = settings.inbox_dir / f"{digest[:12]}_{safe_name}"
    temp_path.write_bytes(content)

    batch = ImportBatch(
        source_filename=safe_name,
        stored_path=str(temp_path),
        file_hash=digest,
        account_id=account_id,
        status=ImportStatus.PENDING.value,
        import_mode=mode,
    )
    db.add(batch)
    db.flush()

    imported = 0
    skipped = 0
    try:
        rows = get_importer(temp_path).parse(temp_path)

        if mode == "human-check":
            for sequence, row in enumerate(rows, start=1):
                result = classify(row.description, db)
                original = row.raw_data or row.description
                db.add(
                    HumanCheckItem(
                        import_batch_id=batch.id,
                        sequence=sequence,
                        original_text=str(original),
                        parsed_booked_on=row.booked_on,
                        parsed_value_on=row.value_on,
                        parsed_description=row.description,
                        parsed_amount=row.amount,
                        parsed_currency=row.currency,
                        parsed_category=result.category,
                        parsed_confidence=result.confidence,
                        parsed_merchant=result.merchant,
                    )
                )
            batch.status = ImportStatus.HUMAN_CHECK.value
            batch.imported_rows = 0
            batch.skipped_rows = 0
            archive_path = settings.archive_dir / temp_path.name
            shutil.move(temp_path, archive_path)
            batch.stored_path = str(archive_path)
            db.commit()
            return batch, 0, 0, False

        for row in rows:
            uid = source_uid(account_id, row.booked_on, row.amount, row.description)
            already_present = db.scalar(
                select(Transaction.id).where(Transaction.account_id == account_id, Transaction.source_uid == uid)
            )
            if already_present:
                skipped += 1
                continue

            result = classify(row.description, db)
            tx = Transaction(
                account_id=account_id,
                import_batch_id=batch.id,
                booked_on=row.booked_on,
                value_on=row.value_on,
                description=row.description,
                normalized_description=normalize_description(row.description),
                merchant=result.merchant,
                amount=row.amount,
                currency=row.currency,
                source_uid=uid,
                category=result.category,
                category_confidence=result.confidence,
                category_source=result.source,
                raw_data=json.dumps({"raw": row.raw_data}, ensure_ascii=False),
            )
            db.add(tx)
            db.flush()
            reconcile_transaction(db, tx)
            imported += 1

        batch.status = ImportStatus.COMPLETED.value
        batch.imported_rows = imported
        batch.skipped_rows = skipped
        archive_path = settings.archive_dir / temp_path.name
        shutil.move(temp_path, archive_path)
        batch.stored_path = str(archive_path)
        db.commit()
        return batch, imported, skipped, False
    except Exception:
        db.rollback()
        raise

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AccountType(StrEnum):
    BANK = "bank"
    CARD = "card"
    PAYPAL = "paypal"
    SATISPAY = "satispay"
    CASH = "cash"
    OTHER = "other"


class ImportStatus(StrEnum):
    PENDING = "pending"
    HUMAN_CHECK = "human_check"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanCheckDecision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    account_type: Mapped[str] = mapped_column(String(30), default=AccountType.BANK.value)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    parent_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    essential: Mapped[bool] = mapped_column(Boolean, default=False)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default=ImportStatus.PENDING.value)
    import_mode: Mapped[str] = mapped_column(String(30), default="standard")
    imported_rows: Mapped[int] = mapped_column(default=0)
    skipped_rows: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "source_uid", name="uq_transaction_source_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    booked_on: Mapped[date] = mapped_column(Date, index=True)
    value_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    normalized_description: Mapped[str] = mapped_column(Text, index=True)
    merchant: Mapped[str] = mapped_column(String(180), default="", index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    source_uid: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(80), default="Uncategorized", index=True)
    category_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0"))
    category_source: Mapped[str] = mapped_column(String(30), default="automatic")
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)
    is_internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    transfer_pair_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)
    excluded_from_analytics: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    manual_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account: Mapped[Account] = relationship(back_populates="transactions")


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    priority: Mapped[int] = mapped_column(default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_from_manual_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HumanCheckItem(Base):
    __tablename__ = "human_check_items"
    __table_args__ = (UniqueConstraint("import_batch_id", "sequence", name="uq_human_check_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    sequence: Mapped[int] = mapped_column(index=True)
    original_text: Mapped[str] = mapped_column(Text)
    parsed_booked_on: Mapped[date] = mapped_column(Date)
    parsed_value_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    parsed_description: Mapped[str] = mapped_column(Text)
    parsed_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    parsed_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    parsed_category: Mapped[str] = mapped_column(String(80), default="Uncategorized")
    parsed_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0"))
    parsed_merchant: Mapped[str] = mapped_column(String(180), default="")
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_cadence: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    decision: Mapped[str] = mapped_column(String(20), default=HumanCheckDecision.PENDING.value, index=True)
    corrected_booked_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    corrected_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    corrected_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("month", "category", name="uq_budget_month_category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RecurrenceOverride(Base):
    __tablename__ = "recurrence_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    merchant: Mapped[str] = mapped_column(String(180), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="confirmed", index=True)
    override_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    override_next_expected: Mapped[date | None] = mapped_column(Date, nullable=True)
    override_cadence: Mapped[str | None] = mapped_column(String(30), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

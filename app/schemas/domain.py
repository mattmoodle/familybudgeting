from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_type: str = "bank"
    currency: str = "EUR"


class AccountRead(AccountCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active: bool


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_name: str | None = Field(default=None, max_length=80)
    essential: bool = False


class CategoryRead(CategoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TransactionPatch(BaseModel):
    category: str | None = None
    excluded_from_analytics: bool | None = None
    manual_note: str | None = None
    create_rule: bool = False
    is_suspicious: bool | None = None
    is_recurring: bool | None = None
    recurrence_cadence: str | None = Field(default=None, pattern=r"^(weekly|biweekly|monthly|bimonthly|quarterly|semiannual|annual)$")


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    booked_on: date
    description: str
    amount: Decimal
    category: str
    category_confidence: Decimal
    category_source: str
    is_duplicate: bool
    is_internal_transfer: bool
    excluded_from_analytics: bool
    account_id: int
    is_suspicious: bool


class ImportResult(BaseModel):
    batch_id: int
    imported: int
    skipped: int
    duplicate_file: bool = False
    import_mode: str = "standard"
    review_url: str | None = None


class HumanCheckDecisionPatch(BaseModel):
    decision: str
    apply_manual_correction: bool = False
    booked_on: date | None = None
    description: str | None = Field(default=None, min_length=1)
    amount: Decimal | None = None
    category: str | None = Field(default=None, min_length=1)
    is_recurring: bool = False
    recurrence_cadence: str | None = Field(default=None, pattern=r"^(weekly|biweekly|monthly|bimonthly|quarterly|semiannual|annual)$")
    is_suspicious: bool = False


class HumanCheckCorrection(BaseModel):
    booked_on: date
    description: str = Field(min_length=1)
    amount: Decimal
    category: str = Field(min_length=1)
    note: str | None = None
    is_recurring: bool = False
    recurrence_cadence: str | None = Field(default=None, pattern=r"^(weekly|biweekly|monthly|bimonthly|quarterly|semiannual|annual)$")
    is_suspicious: bool = False


class HumanCheckItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    import_batch_id: int
    sequence: int
    original_text: str
    parsed_booked_on: date
    parsed_description: str
    parsed_amount: Decimal
    parsed_currency: str
    parsed_category: str
    parsed_confidence: Decimal
    parsed_merchant: str
    decision: str
    is_recurring: bool
    recurrence_cadence: str | None
    is_suspicious: bool


class MonthlyCategoryStat(BaseModel):
    month: str
    category: str
    amount: Decimal


class SavingSuggestion(BaseModel):
    title: str
    explanation: str
    estimated_monthly_saving: Decimal
    confidence: str


class DashboardSummary(BaseModel):
    period_start: date | None
    period_end: date | None
    income: Decimal
    expenses: Decimal
    net_cashflow: Decimal
    savings_rate: Decimal | None
    excluded_internal_transfers: Decimal
    duplicate_rows: int
    generated_at: datetime


class RecurringPatternRead(BaseModel):
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


class ForecastItemRead(BaseModel):
    expected_on: date
    merchant: str
    category: str
    estimated_amount: Decimal
    cadence: str
    cost_type: str
    confidence: Decimal


class CostStructureRead(BaseModel):
    fixed: Decimal
    variable: Decimal
    occasional: Decimal
    total: Decimal
    fixed_count: int
    variable_count: int
    occasional_count: int


class BudgetUpsert(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    category: str = Field(min_length=1, max_length=80)
    amount: Decimal = Field(ge=0)


class BudgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    month: date
    category: str
    amount: Decimal


class BudgetCopyRequest(BaseModel):
    source_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    target_month: str = Field(pattern=r"^\d{4}-\d{2}$")


class RecurrenceOverrideUpsert(BaseModel):
    pattern_key: str = Field(min_length=1, max_length=300)
    merchant: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=80)
    status: str = Field(pattern=r"^(confirmed|rejected|paused|ended)$")
    override_amount: Decimal | None = Field(default=None, ge=0)
    override_next_expected: date | None = None
    override_cadence: str | None = Field(default=None, pattern=r"^(weekly|biweekly|monthly|bimonthly|quarterly|semiannual|annual)$")
    note: str | None = None


class RecurrenceOverrideRead(RecurrenceOverrideUpsert):
    model_config = ConfigDict(from_attributes=True)
    id: int

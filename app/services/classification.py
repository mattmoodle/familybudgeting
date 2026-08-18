from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Rule, Transaction
from app.services.merchant import normalize_merchant
from app.services.normalization import normalize_description


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: Decimal
    source: str
    merchant: str


DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    ("stipend", "Income"),
    ("naspi", "Income"),
    ("mutuo", "Housing"),
    ("condominio", "Housing"),
    ("ikea", "Home"),
    ("porta hfb", "Home"),
    ("euro futura", "Electronics"),
    ("cheerz", "Photos & Prints"),
    ("shopsi", "Photos & Prints"),
    ("la riserva gramuglia", "Restaurants"),
    ("botton d oro", "Restaurants"),
    ("ristor", "Restaurants"),
    ("pizzeria", "Restaurants"),
    ("supermerc", "Groceries"),
    ("eurospin", "Groceries"),
    ("conad", "Groceries"),
    ("coop", "Groceries"),
    ("il forno", "Groceries"),
    ("fiera di roma", "Parking"),
    ("parcheggio", "Parking"),
    ("prima", "Car Insurance"),
    ("assicur", "Insurance"),
    ("tokyo", "Travel"),
    ("japan", "Travel"),
    ("giappone", "Travel"),
    ("trenitalia", "Transport"),
    ("italo", "Transport"),
    ("benzina", "Transport"),
    ("pagopa", "Taxes & Public Fees"),
    ("farmacia", "Health"),
    ("amazon", "Shopping"),
)


def _learned_category(merchant: str, db: Session) -> Classification | None:
    if not merchant or len(merchant) < 3:
        return None

    examples = db.scalars(
        select(Transaction)
        .where(Transaction.category_source == "manual", Transaction.merchant != "")
        .order_by(Transaction.updated_at.desc())
        .limit(1000)
    ).all()
    if not examples:
        return None

    best: tuple[float, Transaction] | None = None
    for example in examples:
        score = SequenceMatcher(None, merchant, example.merchant).ratio()
        if best is None or score > best[0]:
            best = (score, example)

    if best and best[0] >= 0.78:
        confidence = min(Decimal("0.960"), Decimal(str(round(0.70 + best[0] * 0.25, 3))))
        return Classification(best[1].category, confidence, "learned-local", merchant)
    return None


def classify(description: str, db: Session) -> Classification:
    normalized = normalize_description(description)
    merchant = normalize_merchant(description)

    # Hierarchy: explicit user rule > local learned example > built-in heuristic > fallback.
    manual_rules = db.scalars(select(Rule).where(Rule.active.is_(True)).order_by(Rule.priority.asc())).all()
    for rule in manual_rules:
        needle = normalize_description(rule.pattern)
        if needle and (needle in normalized or needle in merchant):
            return Classification(rule.category, Decimal("0.990"), "rule", merchant)

    learned = _learned_category(merchant, db)
    if learned:
        return learned

    for keyword, category in DEFAULT_RULES:
        if keyword in normalized or keyword in merchant:
            return Classification(category, Decimal("0.850"), "automatic", merchant)
    return Classification("Uncategorized", Decimal("0.100"), "automatic", merchant)


def reclassify_transaction(tx: Transaction, db: Session) -> None:
    if tx.category_source == "manual":
        return
    result = classify(tx.description, db)
    tx.category = result.category
    tx.category_confidence = result.confidence
    tx.category_source = result.source
    tx.merchant = result.merchant

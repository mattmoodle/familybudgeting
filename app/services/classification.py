from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from math import log
from collections import Counter, defaultdict

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


# Payment infrastructure words occur in many unrelated statements and must never
# become the main signal for a learned categorisation rule.
GENERIC_RULE_TOKENS = frozenset({
    "pagamento", "pagamenti", "tramite", "operazione", "operazioni", "pos", "carta",
    "addebito", "transazione", "movimento", "movimenti", "data", "valuta", "del", "della",
    "di", "da", "a", "con", "per", "su", "online", "euro", "eur", "richiesta",
    "incasso", "effetti", "ritirati", "core", "sdd", "sepa",
})


def _rule_tokens(value: str) -> list[str]:
    return [
        token for token in normalize_description(value).split()
        if len(token) >= 3 and not any(character.isdigit() for character in token) and token not in GENERIC_RULE_TOKENS
    ]


def rule_pattern_for_transaction(description: str, db: Session) -> str | None:
    """Extract a compact, distinctive local rule pattern from a corrected transaction.

    Common payment boilerplate is ignored. Among the remaining terms we prefer the
    ones that occur in fewer existing ledger descriptions, which normally identifies
    the merchant rather than the payment channel.
    """
    candidates = _rule_tokens(description)
    if not candidates:
        return None
    documents = db.scalars(select(Transaction.normalized_description).limit(5000)).all()
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(_rule_tokens(document)))
    total_documents = max(1, len(documents))
    scored = [
        (log((total_documents + 1) / (document_frequency[token] + 1)), index, token)
        for index, token in enumerate(candidates)
    ]
    # One distinctive merchant token is safer than a generic four-word prefix.
    _, _, best_token = max(scored, key=lambda item: (item[0], -item[1], len(item[2])))
    return best_token


def _rule_specificity(pattern: str, document_frequency: Counter[str], total_rules: int) -> float:
    tokens = _rule_tokens(pattern)
    if not tokens:
        return 0.0
    return sum(log((total_rules + 1) / (document_frequency[token] + 1)) for token in set(tokens))


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
    # A pattern mapped to several categories is intentionally ignored: picking the first
    # database row would be arbitrary and can silently misclassify many purchases.
    manual_rules = db.scalars(select(Rule).where(Rule.active.is_(True))).all()
    rules_by_pattern: dict[str, list[Rule]] = defaultdict(list)
    for rule in manual_rules:
        needle = normalize_description(rule.pattern)
        if needle:
            rules_by_pattern[needle].append(rule)
    rule_frequency: Counter[str] = Counter()
    for needle in rules_by_pattern:
        rule_frequency.update(set(_rule_tokens(needle)))
    matches: list[tuple[int, float, int, Rule]] = []
    for needle, rules in rules_by_pattern.items():
        if len({rule.category for rule in rules}) > 1:
            continue
        if needle not in normalized and needle not in merchant:
            continue
        representative = min(rules, key=lambda rule: (rule.priority, rule.id))
        matches.append((representative.priority, _rule_specificity(needle, rule_frequency, len(rules_by_pattern)), len(needle), representative))
    if matches:
        _, _, _, rule = min(matches, key=lambda item: (item[0], -item[1], -item[2], item[3].id))
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

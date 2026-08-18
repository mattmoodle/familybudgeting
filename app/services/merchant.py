from __future__ import annotations

import re

from app.services.normalization import normalize_description

NOISE_PATTERNS = (
    r"\bpagamento\b",
    r"\bacquisto\b",
    r"\bpos\b",
    r"\bcarta\b",
    r"\bpaypal\b",
    r"\bsatispay\b",
    r"\baddebito\b",
    r"\bsepa\b",
    r"\bbonifico\b",
    r"\boperazione\b",
    r"\btransazione\b",
    r"\bdata\b",
    r"\bvaluta\b",
    r"\bit\d{2}[a-z0-9]{10,}\b",
    r"\b\d{2}/\d{2}/\d{2,4}\b",
    r"\b\d{4,}\b",
)

ALIASES = {
    "amazon eu sarl": "amazon",
    "amazon marketplace": "amazon",
    "ikea italia retail": "ikea",
    "euro futura srl": "euro futura",
    "la riserva gramuglia": "la riserva gramuglia",
    "botton d oro": "botton d oro",
    "fiera di roma": "fiera di roma",
}


def normalize_merchant(description: str) -> str:
    text = normalize_description(description)
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"\b(eur|euro)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_/.,")

    for alias, canonical in ALIASES.items():
        if alias in text:
            return canonical

    # Keep a compact, stable identity rather than volatile payment metadata.
    tokens = [t for t in text.split() if len(t) > 1]
    return " ".join(tokens[:6]) or normalize_description(description)[:120]

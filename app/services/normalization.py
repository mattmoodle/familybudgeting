import hashlib
import re
import unicodedata
from datetime import date
from decimal import Decimal


SPACE_RE = re.compile(r"\s+")
NOISE_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_description(value: str) -> str:
    value = (value or "").replace("’", " ").replace("‘", " ").replace("\'", " ")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = NOISE_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def source_uid(account_id: int, booked_on: date, amount: Decimal, description: str) -> str:
    payload = f"{account_id}|{booked_on.isoformat()}|{amount:.2f}|{normalize_description(description)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

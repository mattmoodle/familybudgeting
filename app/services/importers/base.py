from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


@dataclass(slots=True)
class ImportedRow:
    booked_on: date
    description: str
    amount: Decimal
    value_on: date | None = None
    currency: str = "EUR"
    raw_data: str | None = None


class StatementImporter(ABC):
    @abstractmethod
    def parse(self, path: Path) -> list[ImportedRow]:
        raise NotImplementedError

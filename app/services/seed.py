from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Category

DEFAULT_CATEGORIES = [
    ("Income", None, True),
    ("Housing", None, True),
    ("Groceries", None, True),
    ("Restaurants", None, False),
    ("Transport", None, True),
    ("Parking", "Transport", False),
    ("Car Insurance", "Transport", True),
    ("Home", None, False),
    ("Electronics", "Shopping", False),
    ("Shopping", None, False),
    ("Photos & Prints", "Shopping", False),
    ("Travel", None, False),
    ("Insurance", None, True),
    ("Health", None, True),
    ("Taxes & Public Fees", None, True),
    ("Uncategorized", None, False),
]


def seed_categories(db: Session) -> None:
    existing = set(db.scalars(select(Category.name)).all())
    for name, parent, essential in DEFAULT_CATEGORIES:
        if name not in existing:
            db.add(Category(name=name, parent_name=parent, essential=essential))
    db.commit()

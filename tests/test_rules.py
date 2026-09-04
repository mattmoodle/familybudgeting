from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes import create_rule, delete_rule, rules_page, update_rule
from app.db.base import Base
from app.models.entities import Category
from app.schemas.domain import RuleCreate, RuleUpdate


def test_rules_can_be_created_filtered_edited_and_deleted():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Category(name="Subscriptions"), Category(name="Shopping")])
        db.commit()

        audible = create_rule(RuleCreate(pattern="audible europe", category="Subscriptions", priority=20), db)
        shopping = create_rule(RuleCreate(pattern="amazon", category="Shopping", priority=10), db)
        updated = update_rule(audible.id, RuleUpdate(category="Shopping", active=False, priority=5), db)
        assert updated.category == "Shopping"
        assert updated.active is False
        assert updated.priority == 5

        request = Request({"type": "http", "method": "GET", "path": "/rules", "headers": [], "query_string": b"rules_status=active&rules_sort=priority"})
        rendered = rules_page(request=request, rules_status="active", rules_sort="priority", db=db).body.decode()
        assert "amazon" in rendered
        assert "audible europe" not in rendered

        assert delete_rule(shopping.id, db) == {"deleted": True, "rule_id": shopping.id}

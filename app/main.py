from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.db.base import Base
from app.db.migrations import ensure_schema_compatibility
from app.db.session import SessionLocal, engine
from app.services.seed import seed_categories


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Privacy-first offline family budgeting and transaction reconciliation platform.",
        version="0.7.1",
    )
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(router)

    @app.on_event("startup")
    def startup() -> None:
        settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        settings.archive_dir.mkdir(parents=True, exist_ok=True)
        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(bind=engine)
        ensure_schema_compatibility(engine)
        with SessionLocal() as db:
            seed_categories(db)

    return app


app = create_app()

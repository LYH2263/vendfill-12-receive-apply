from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api.router import api_router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_if_empty


def _ensure_refill_status_columns(eng=None) -> None:
    """老库补列：create_all 不会改已有表，核销依赖 status / verified_at。"""
    eng = eng or engine
    insp = inspect(eng)
    if not insp.has_table("refill_orders"):
        return
    cols = {c["name"] for c in insp.get_columns("refill_orders")}
    ts_type = "TIMESTAMP" if eng.dialect.name == "postgresql" else "DATETIME"
    with eng.begin() as conn:
        if "status" not in cols:
            conn.execute(text(
                "ALTER TABLE refill_orders ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'open'"))
        if "verified_at" not in cols:
            conn.execute(text(
                f"ALTER TABLE refill_orders ADD COLUMN verified_at {ts_type}"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_refill_status_columns()
    if settings.seed_on_empty:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    yield


app = FastAPI(title="VendFill", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")

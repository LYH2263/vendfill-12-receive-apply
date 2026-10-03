import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import build_fill_lines, summarize
from app.services.refill_service import VerifyError, live_snapshot, verify_refill, void_refill
router = APIRouter(prefix="/refills", tags=["refills"])

def _compute(db: Session, location_id: int) -> dict:
    lanes = db.scalars(select(Lane).where(Lane.location_id == location_id).order_by(Lane.slot_no)).all()
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]
    return summarize(build_fill_lines(payload))

def _serialize(order: RefillOrder) -> dict:
    stored = json.loads(order.lines_json or "{}")
    return {
        "id": order.id,
        "location_id": order.location_id,
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "verified_at": order.verified_at.isoformat() if order.verified_at else None,
        "total_fill": stored.get("total_fill", 0),
        "need_fill_count": stored.get("need_fill_count", 0),
        "line_count": len(stored.get("lines", [])),
    }

@router.post("/run")
def run_refill(location_id: int = 1, db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if not loc: raise HTTPException(404, "点位不存在")
    summary = _compute(db, location_id)
    order = RefillOrder(location_id=location_id, created_at=datetime.utcnow(), status="open",
                        lines_json=json.dumps(summary, ensure_ascii=False))
    db.add(order); db.commit(); db.refresh(order)
    return {"id": order.id, "location_id": location_id, "status": order.status,
            "verified_at": None, **summary}

@router.get("/latest")
def latest(location_id: int = 1, db: Session = Depends(get_db)):
    order = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                       .order_by(RefillOrder.id.desc())).first()
    if not order:
        return run_refill(location_id=location_id, db=db)
    # 行与汇总始终按当前库存/在途实时重算，保证货道、核销状态、汇总、满仓同一跳变。
    return live_snapshot(db, order)

@router.get("/full")
def full_lanes(location_id: int = 1, db: Session = Depends(get_db)):
    data = latest(location_id=location_id, db=db)
    return {"location_id": location_id, "lanes": [l for l in data["lines"] if l["status"] == "full"]}

@router.get("/summary")
def refill_summary(location_id: int = 1, db: Session = Depends(get_db)):
    data = latest(location_id=location_id, db=db)
    return {
        "location_id": location_id,
        "order_id": data["id"],
        "status": data["status"],
        "total_fill": data["total_fill"],
        "need_fill_count": data["need_fill_count"],
        "full_count": data["full_count"],
        "overbooked_count": data["overbooked_count"],
    }

@router.get("/orders")
def list_orders(location_id: int = 1, db: Session = Depends(get_db)):
    rows = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                      .order_by(RefillOrder.id.desc())).all()
    return [_serialize(o) for o in rows]

@router.get("/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(RefillOrder, order_id)
    if not order:
        raise HTTPException(404, "补货单不存在")
    stored = json.loads(order.lines_json or "{}")
    live = live_snapshot(db, order)  # 实时货道：核销后立刻反映新缺口/满仓
    return {
        "id": order.id,
        "location_id": order.location_id,
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "verified_at": order.verified_at.isoformat() if order.verified_at else None,
        "lines": stored.get("lines", []),
        "total_fill": stored.get("total_fill", 0),
        "live": {
            "total_fill": live["total_fill"],
            "need_fill_count": live["need_fill_count"],
            "full_count": live["full_count"],
            "overbooked_count": live["overbooked_count"],
            "lines": live["lines"],
        },
    }

@router.post("/{order_id}/verify")
def verify(order_id: int, db: Session = Depends(get_db)):
    try:
        return verify_refill(db, order_id)
    except VerifyError as exc:
        raise HTTPException(409, str(exc))

@router.post("/{order_id}/void")
def void(order_id: int, db: Session = Depends(get_db)):
    try:
        order = void_refill(db, order_id)
    except VerifyError as exc:
        raise HTTPException(409, str(exc))
    return _serialize(order)

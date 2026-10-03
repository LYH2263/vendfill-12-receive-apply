import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import apply_arrival, build_fill_lines, summarize

router = APIRouter(prefix="/refills", tags=["refills"])

STATUS_PENDING = "pending"    # 待核销
STATUS_VERIFIED = "verified"  # 已核销
STATUS_VOID = "void"          # 已作废


def _lane_payload(location_id: int, db: Session) -> list[dict]:
    lanes = db.scalars(select(Lane).where(Lane.location_id == location_id).order_by(Lane.slot_no)).all()
    return [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
             "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]


def _live_summary(location_id: int, db: Session) -> dict:
    """汇总/满仓永远从货道现值计算：与货道页同一数据源，核销后同一跳变，不分叉。"""
    return summarize(build_fill_lines(_lane_payload(location_id, db)))


def _order_out(order: RefillOrder, data: dict) -> dict:
    return {
        "id": order.id,
        "location_id": order.location_id,
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "verified_at": order.verified_at.isoformat() if order.verified_at else None,
        **data,
    }


def _get_order(order_id: int, db: Session) -> RefillOrder:
    # 行锁（Postgres 生效，SQLite 忽略）：并发核销串行化，第二张核销必看到已核销
    order = db.scalar(select(RefillOrder).where(RefillOrder.id == order_id).with_for_update())
    if not order:
        raise HTTPException(404, "补货单不存在")
    return order


@router.post("/run")
def run_refill(location_id: int = 1, db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if not loc: raise HTTPException(404, "点位不存在")
    summary = _live_summary(location_id, db)
    order = RefillOrder(location_id=location_id, created_at=datetime.utcnow(),
                        lines_json=json.dumps(summary, ensure_ascii=False), status=STATUS_PENDING)
    db.add(order); db.commit(); db.refresh(order)
    return _order_out(order, summary)


@router.get("/preview")
def preview(location_id: int = 1, db: Session = Depends(get_db)):
    """实时补货建议，不落单。"""
    loc = db.get(Location, location_id)
    if not loc: raise HTTPException(404, "点位不存在")
    return {"location_id": location_id, **_live_summary(location_id, db)}


@router.get("/latest")
def latest(location_id: int = 1, db: Session = Depends(get_db)):
    order = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                       .order_by(RefillOrder.id.desc())).first()
    if not order:
        return run_refill(location_id=location_id, db=db)
    return _order_out(order, json.loads(order.lines_json))


@router.get("/orders")
def list_orders(location_id: int = 1, db: Session = Depends(get_db)):
    rows = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                      .order_by(RefillOrder.id.desc())).all()
    out = []
    for o in rows:
        try:
            data = json.loads(o.lines_json)
        except Exception:
            data = {}
        out.append({"id": o.id, "location_id": o.location_id, "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                    "verified_at": o.verified_at.isoformat() if o.verified_at else None,
                    "total_fill": data.get("total_fill", 0)})
    return out


@router.get("/full")
def full_lanes(location_id: int = 1, db: Session = Depends(get_db)):
    data = _live_summary(location_id, db)
    return {"location_id": location_id, "lanes": [l for l in data["lines"] if l["status"] == "full"]}


@router.get("/summary")
def refill_summary(location_id: int = 1, db: Session = Depends(get_db)):
    data = _live_summary(location_id, db)
    return {
        "location_id": location_id,
        "total_fill": data["total_fill"],
        "need_fill_count": data["need_fill_count"],
        "full_count": data["full_count"],
        "overbooked_count": data["overbooked_count"],
    }


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(RefillOrder, order_id)
    if not order:
        raise HTTPException(404, "补货单不存在")
    return _order_out(order, json.loads(order.lines_json))


@router.post("/{order_id}/verify")
def verify_refill(order_id: int, db: Session = Depends(get_db)):
    """到货核销：按行 库存 += 补量、在途扣减（不为负），单标记已核销。

    货道数字与单据状态在同一事务提交：一起跳变或一起回退，
    禁止只改库存不改在途、只改单据不改货道。已核销/已作废/货道缺失一律拒绝且三方不动。
    """
    order = _get_order(order_id, db)
    if order.status == STATUS_VERIFIED:
        raise HTTPException(409, "补货单已核销，请勿重复核销")
    if order.status == STATUS_VOID:
        raise HTTPException(409, "补货单已作废，不能核销")
    try:
        payload = json.loads(order.lines_json)
        lines = payload.get("lines", []) if isinstance(payload, dict) else []
        lane_ids = [int(line["lane_id"]) for line in lines]
    except (KeyError, TypeError, ValueError):
        db.rollback()
        raise HTTPException(500, "补货单数据损坏，无法核销")
    # 先校验全部货道存在，再动任何数字：缺任一道即整单拒绝，库存/在途/单据全部留在原地
    lanes = ({l.id: l for l in db.scalars(select(Lane).where(Lane.id.in_(lane_ids))).all()}
             if lane_ids else {})
    missing = [lid for lid in lane_ids if lid not in lanes]
    if missing:
        db.rollback()
        raise HTTPException(409, f"货道缺失，核销已取消: {missing}")
    try:
        for line in lines:
            qty = int(line.get("fill_qty", 0) or 0)
            if qty <= 0:
                continue
            lane = lanes[int(line["lane_id"])]
            lane.stock, lane.in_transit = apply_arrival(lane.stock, lane.in_transit, qty)
        order.status = STATUS_VERIFIED
        order.verified_at = datetime.utcnow()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "核销失败，已回滚")
    db.refresh(order)
    return {
        "id": order.id,
        "location_id": order.location_id,
        "status": order.status,
        "verified_at": order.verified_at.isoformat() if order.verified_at else None,
        "summary": refill_summary(location_id=order.location_id, db=db),
    }


@router.post("/{order_id}/void")
def void_refill(order_id: int, db: Session = Depends(get_db)):
    order = _get_order(order_id, db)
    if order.status == STATUS_VERIFIED:
        raise HTTPException(409, "补货单已核销，不能作废")
    if order.status == STATUS_VOID:
        raise HTTPException(409, "补货单已作废，请勿重复作废")
    order.status = STATUS_VOID
    db.commit()
    return {"id": order.id, "status": order.status}

"""到货核销：按行把补量加进货道库存，并从在途扣去不超过补量的部分。

库存、在途、单据状态在同一事务同一次提交里跳变；任一前提不满足则整体
回滚，三处（货道数字、单据状态、由实时货道算出的汇总/满仓）不会分叉。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Lane, RefillOrder
from app.services.fill_engine import build_fill_lines, summarize


class VerifyError(Exception):
    """核销被拒绝（单据已核销 / 已作废 / 货道缺失）。"""


def _snapshot_order(db: Session, order: RefillOrder) -> dict:
    lanes = db.scalars(
        select(Lane).where(Lane.location_id == order.location_id).order_by(Lane.slot_no)
    ).all()
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit}
               for l in lanes]
    summary = summarize(build_fill_lines(payload))
    return {"id": order.id, "location_id": order.location_id,
            "status": order.status,
            "verified_at": order.verified_at.isoformat() if order.verified_at else None,
            **summary}


def live_snapshot(db: Session, order: RefillOrder) -> dict:
    """单据头 + 按当前库存在途实时算出的汇总与满仓集合。"""
    return _snapshot_order(db, order)


def verify_refill(db: Session, order_id: int) -> dict:
    """核销一张未作废且未核销的补货单。

    每个正补量行：lane.stock += fill_qty；
    lane.in_transit -= min(lane.in_transit, fill_qty)（不足扣到 0，绝不为负）。
    成功后单据置为 verified。任何失败整体回滚，调用方看到的状态与操作前一致。
    """
    order = db.get(RefillOrder, order_id)
    if order is None:
        raise VerifyError("补货单不存在")
    # 先判状态再动数据；拒绝时本事务内什么都没改过，回滚后三处不动。
    if order.status == "verified":
        raise VerifyError("补货单已核销，不能重复核销")
    if order.status == "void":
        raise VerifyError("补货单已作废，不能核销")

    data = json.loads(order.lines_json or "[]")
    lines = data.get("lines", []) if isinstance(data, dict) else []
    try:
        for line in lines:
            fill_qty = int(line.get("fill_qty", 0))
            if fill_qty <= 0:
                continue
            lane = db.get(Lane, int(line["lane_id"]))
            if lane is None:
                raise VerifyError(f"货道不存在：{line.get('slot_no', line.get('lane_id'))}")
            lane.stock = int(lane.stock) + fill_qty
            lane.in_transit = max(0, int(lane.in_transit) - fill_qty)
        order.status = "verified"
        order.verified_at = datetime.utcnow()
        # 单据库存与状态在同一次 commit 中落库：要么一起跳变，要么一起退回。
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return _snapshot_order(db, order)


def void_refill(db: Session, order_id: int) -> RefillOrder:
    """作废一张尚未核销的补货单；已核销单不得作废。"""
    order = db.get(RefillOrder, order_id)
    if order is None:
        raise VerifyError("补货单不存在")
    if order.status == "verified":
        raise VerifyError("补货单已核销，不能作废")
    if order.status == "void":
        raise VerifyError("补货单已作废")
    order.status = "void"
    db.commit()
    db.refresh(order)
    return order

import json
from datetime import datetime, timedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.models import Lane, Location, RefillOrder, Sale
from app.services.fill_engine import build_fill_lines, summarize

def seed_if_empty(db: Session) -> None:
    if (db.scalar(select(func.count()).select_from(Location)) or 0) > 0:
        return
    loc = Location(code="VM-01", name="地铁口 A 点位", address="城东地铁 1 号口")
    db.add(loc); db.flush()
    lanes = [
        ("A1", "矿泉水", 20, 5, 0),
        ("A2", "可乐", 18, 18, 0),
        ("B1", "薯片", 12, 3, 2),
        ("B2", "巧克力", 15, 10, 5),
        ("C1", "能量棒", 10, 0, 0),
        ("C2", "口香糖", 24, 24, 2),
    ]
    lane_rows = []
    for slot, sku, cap, stock, transit in lanes:
        lane = Lane(location_id=loc.id, slot_no=slot, sku_name=sku, capacity=cap, stock=stock, in_transit=transit)
        db.add(lane); db.flush()
        lane_rows.append(lane)
    now = datetime(2026, 9, 16, 12, 0, 0)
    for i, lane in enumerate(lane_rows):
        db.add(Sale(lane_id=lane.id, qty=2 + i, sold_at=now - timedelta(hours=i)))
    # 待核销补货单：按当前货道缺口生成（B1 缺 7、补 7，在途 2），核销后库存升、在途降
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lane_rows]
    summary = summarize(build_fill_lines(payload))
    db.add(RefillOrder(location_id=loc.id, created_at=now,
                       lines_json=json.dumps(summary, ensure_ascii=False), status="pending"))
    db.commit()

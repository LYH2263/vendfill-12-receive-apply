"""HTTP 层：核销接口的同一跳变与 409 拒绝。"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import build_fill_lines, summarize


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    loc = Location(code="VM-01", name="测试点位")
    s.add(loc); s.flush()
    for slot, sku, cap, stock, transit in [
        ("A1", "矿泉水", 20, 5, 0), ("A2", "可乐", 18, 18, 0),
        ("B1", "薯片", 12, 3, 2), ("B2", "巧克力", 15, 10, 5),
        ("C1", "能量棒", 10, 0, 0), ("C2", "口香糖", 24, 24, 2),
    ]:
        s.add(Lane(location_id=loc.id, slot_no=slot, sku_name=sku,
                   capacity=cap, stock=stock, in_transit=transit))
    s.commit()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _make_order(Session) -> int:
    s = Session()
    loc = s.scalars(select(Location)).one()
    lanes = s.scalars(select(Lane).order_by(Lane.slot_no)).all()
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]
    order = RefillOrder(location_id=loc.id, lines_json=json.dumps(summarize(build_fill_lines(payload)), ensure_ascii=False))
    s.add(order); s.commit()
    oid = order.id
    s.close()
    return oid


def test_verify_then_reverify_409_and_summary_consistent(client):
    c, Session = client
    oid = _make_order(Session)

    r1 = c.post(f"/api/refills/{oid}/verify")
    assert r1.status_code == 200
    body = r1.json()
    assert body["status"] == "verified"
    b1 = [l for l in body["lines"] if l["slot_no"] == "B1"][0]
    assert (b1["stock"], b1["in_transit"], b1["gap"]) == (10, 0, 2)

    # 汇总与满仓立刻与新库存在途一致
    summ = c.get("/api/refills/summary?location_id=1").json()
    assert (summ["total_fill"], summ["need_fill_count"], summ["status"]) == (2, 1, "verified")
    full = c.get("/api/refills/full?location_id=1").json()
    assert {l["slot_no"] for l in full["lanes"]} == {"A1", "A2", "B2", "C1"}

    # 已核销单再核：明确 409 拒绝，三处不动
    r2 = c.post(f"/api/refills/{oid}/verify")
    assert r2.status_code == 409
    s = Session()
    lane_state = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}
    assert lane_state["B1"] == (10, 0)
    assert lane_state["A1"] == (20, 0)
    assert s.get(RefillOrder, oid).status == "verified"
    s.close()


def test_verify_void_order_409_then_verify_then_drift(client):
    c, Session = client
    oid = _make_order(Session)
    assert c.post(f"/api/refills/{oid}/void").status_code == 200
    r = c.post(f"/api/refills/{oid}/verify")
    assert r.status_code == 409
    s = Session()
    # 库存在途全部保持种子原值
    assert {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()} == {
        "A1": (5, 0), "A2": (18, 0), "B1": (3, 2), "B2": (10, 5),
        "C1": (0, 0), "C2": (24, 2),
    }
    assert s.get(RefillOrder, oid).status == "void"
    s.close()


def test_missing_lane_409_and_rollback_via_http(client):
    c, Session = client
    oid = _make_order(Session)
    s = Session()
    s.delete(s.scalars(select(Lane).where(Lane.slot_no == "C1")).one())
    s.commit(); s.close()

    r = c.post(f"/api/refills/{oid}/verify")
    assert r.status_code == 409

    s = Session()
    assert {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()} == {
        "A1": (5, 0), "A2": (18, 0), "B1": (3, 2), "B2": (10, 5), "C2": (24, 2),
    }
    assert s.get(RefillOrder, oid).status == "open"
    s.close()

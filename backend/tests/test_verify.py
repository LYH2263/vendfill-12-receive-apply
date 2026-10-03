"""到货核销：库存/在途/单据状态/汇总/满仓同一跳变；失败整体回退；重复核销拒绝且不漂移。"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import build_fill_lines, summarize

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

LANE_SPECS = [  # slot, sku, capacity, stock, in_transit
    ("A1", "矿泉水", 20, 5, 0),
    ("B1", "薯片", 12, 3, 2),   # 缺 7、补 7、在途 2：核销后 库存 10 / 在途 0
    ("C1", "能量棒", 10, 0, 0),
    ("D1", "可乐", 10, 10, 0),
]


@pytest.fixture()
def client():
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        s = TestingSessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def _make_world(status="pending", lines_json=None):
    """造一个点位、四条货道、一张补货单；返回 (location_id, order_id, {slot: lane_id})。"""
    s = TestingSessionLocal()
    loc = Location(code="VM-T", name="测试点位", address="")
    s.add(loc)
    s.flush()
    lane_ids = {}
    payload = []
    for slot, sku, cap, stock, transit in LANE_SPECS:
        lane = Lane(location_id=loc.id, slot_no=slot, sku_name=sku,
                    capacity=cap, stock=stock, in_transit=transit)
        s.add(lane)
        s.flush()
        lane_ids[slot] = lane.id
        payload.append({"id": lane.id, "slot_no": slot, "sku_name": sku,
                        "capacity": cap, "stock": stock, "in_transit": transit})
    if lines_json is None:
        lines_json = json.dumps(summarize(build_fill_lines(payload)), ensure_ascii=False)
    order = RefillOrder(location_id=loc.id, created_at=datetime(2026, 9, 16, 12),
                        lines_json=lines_json, status=status)
    s.add(order)
    s.commit()
    out = (loc.id, order.id, lane_ids)
    s.close()
    return out


def _lanes(client, loc_id):
    return {r["slot_no"]: r for r in client.get(f"/api/lanes?location_id={loc_id}").json()}


def test_verify_success_single_jump(client):
    loc_id, oid, _ = _make_world()
    before = _lanes(client, loc_id)
    assert before["B1"]["stock"] == 3 and before["B1"]["in_transit"] == 2

    r = client.post(f"/api/refills/{oid}/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "verified"
    assert body["verified_at"]

    # 货道页：B1 库存升、在途降（在途 2 < 补量 7，扣到 0 不为负）
    lanes = _lanes(client, loc_id)
    assert lanes["B1"]["stock"] == 10
    assert lanes["B1"]["in_transit"] == 0
    assert lanes["A1"]["stock"] == 20 and lanes["A1"]["in_transit"] == 0
    assert lanes["C1"]["stock"] == 10

    # 汇总按新缺口变：只剩 B1 缺 2；满仓集合 = A1/C1/D1
    s = client.get(f"/api/refills/summary?location_id={loc_id}").json()
    assert s["total_fill"] == 2
    assert s["need_fill_count"] == 1
    assert s["full_count"] == 3
    assert body["summary"] == s  # 核销响应与汇总同源，同一跳变

    full = client.get(f"/api/refills/full?location_id={loc_id}").json()["lanes"]
    assert {l["slot_no"] for l in full} == {"A1", "C1", "D1"}

    # 单据状态已核销，且单据原文（各行补量）不被改写
    order = client.get(f"/api/refills/{oid}").json()
    assert order["status"] == "verified"
    b1_line = [l for l in order["lines"] if l["slot_no"] == "B1"][0]
    assert b1_line["fill_qty"] == 7


def test_verify_twice_rejected_no_drift(client):
    loc_id, oid, _ = _make_world()
    assert client.post(f"/api/refills/{oid}/verify").status_code == 200
    lanes_after = _lanes(client, loc_id)
    summary_after = client.get(f"/api/refills/summary?location_id={loc_id}").json()

    r = client.post(f"/api/refills/{oid}/verify")
    assert r.status_code == 409

    # 三处不动：货道、汇总、单据状态都不漂移
    assert _lanes(client, loc_id) == lanes_after
    assert client.get(f"/api/refills/summary?location_id={loc_id}").json() == summary_after
    assert client.get(f"/api/refills/{oid}").json()["status"] == "verified"


def test_void_then_verify_rejected_nothing_moves(client):
    loc_id, oid, _ = _make_world()
    before = _lanes(client, loc_id)

    r = client.post(f"/api/refills/{oid}/void")
    assert r.status_code == 200 and r.json()["status"] == "void"

    r = client.post(f"/api/refills/{oid}/verify")
    assert r.status_code == 409
    assert _lanes(client, loc_id) == before
    assert client.get(f"/api/refills/{oid}").json()["status"] == "void"

    # 重复作废同样拒绝
    assert client.post(f"/api/refills/{oid}/void").status_code == 409


def test_verified_order_cannot_be_voided(client):
    loc_id, oid, _ = _make_world()
    assert client.post(f"/api/refills/{oid}/verify").status_code == 200
    assert client.post(f"/api/refills/{oid}/void").status_code == 409
    assert client.get(f"/api/refills/{oid}").json()["status"] == "verified"


def test_missing_lane_full_rollback(client):
    loc_id, _, lane_ids = _make_world()
    bad = {
        "total_fill": 8, "need_fill_count": 2, "full_count": 0, "overbooked_count": 0,
        "lines": [
            {"lane_id": lane_ids["A1"], "slot_no": "A1", "sku_name": "矿泉水",
             "capacity": 20, "stock": 5, "in_transit": 0, "gap": 15, "fill_qty": 5, "status": "need_fill"},
            {"lane_id": 99999, "slot_no": "Z9", "sku_name": "不存在",
             "capacity": 10, "stock": 0, "in_transit": 0, "gap": 10, "fill_qty": 3, "status": "need_fill"},
        ],
    }
    s = TestingSessionLocal()
    order = RefillOrder(location_id=loc_id, created_at=datetime(2026, 9, 17, 8),
                        lines_json=json.dumps(bad, ensure_ascii=False), status="pending")
    s.add(order)
    s.commit()
    oid = order.id
    s.close()

    before = _lanes(client, loc_id)
    r = client.post(f"/api/refills/{oid}/verify")
    assert r.status_code == 409

    # 整体回退：有效行 A1 也不许动，单据仍是待核销
    assert _lanes(client, loc_id) == before
    assert client.get(f"/api/refills/{oid}").json()["status"] == "pending"


def test_other_orders_untouched(client):
    loc_id, oid1, _ = _make_world()
    marker = json.dumps({"total_fill": 1, "lines": [{"lane_id": 1, "fill_qty": 1}]},
                        ensure_ascii=False)
    s = TestingSessionLocal()
    older = RefillOrder(location_id=loc_id, created_at=datetime(2026, 9, 15, 9),
                        lines_json=marker, status="pending")
    s.add(older)
    s.commit()
    older_id = older.id
    s.close()

    assert client.post(f"/api/refills/{oid1}/verify").status_code == 200

    # 更早落下的单：内容与状态一字不动
    s = TestingSessionLocal()
    other = s.get(RefillOrder, older_id)
    assert other.lines_json == marker
    assert other.status == "pending"
    s.close()


def test_verify_unknown_order_404(client):
    _make_world()
    assert client.post("/api/refills/424242/verify").status_code == 404


def test_seeded_world_summary_and_full_are_live(client):
    """汇总/满仓与货道页同源：核销一跳，三处同时变。"""
    loc_id, oid, _ = _make_world()
    s0 = client.get(f"/api/refills/summary?location_id={loc_id}").json()
    assert s0["total_fill"] == 32 and s0["need_fill_count"] == 3 and s0["full_count"] == 1
    full0 = client.get(f"/api/refills/full?location_id={loc_id}").json()["lanes"]
    assert {l["slot_no"] for l in full0} == {"D1"}

    client.post(f"/api/refills/{oid}/verify")

    s1 = client.get(f"/api/refills/summary?location_id={loc_id}").json()
    assert s1["total_fill"] == 2 and s1["need_fill_count"] == 1 and s1["full_count"] == 3
    # 与货道页现值逐道一致
    lanes = _lanes(client, loc_id)
    for slot, expect_gap in {"A1": 0, "B1": 2, "C1": 0, "D1": 0}.items():
        assert lanes[slot]["gap"] == expect_gap

"""核销原子性测试：货道库存/在途、单据状态、汇总与满仓必须同一跳变。"""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import build_fill_lines, summarize
from app.services.refill_service import VerifyError, live_snapshot, verify_refill, void_refill


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    loc = Location(code="VM-01", name="测试点位")
    s.add(loc)
    s.flush()
    # 与种子一致：B1 容量12 库存3 在途2 -> 缺口7 正补量
    for slot, sku, cap, stock, transit in [
        ("A1", "矿泉水", 20, 5, 0),
        ("A2", "可乐", 18, 18, 0),
        ("B1", "薯片", 12, 3, 2),
        ("B2", "巧克力", 15, 10, 5),
        ("C1", "能量棒", 10, 0, 0),
        ("C2", "口香糖", 24, 24, 2),
    ]:
        s.add(Lane(location_id=loc.id, slot_no=slot, sku_name=sku,
                   capacity=cap, stock=stock, in_transit=transit))
    s.commit()
    yield s, loc
    s.close()


def _make_order(s, loc) -> RefillOrder:
    lanes = s.scalars(select(Lane).where(Lane.location_id == loc.id).order_by(Lane.slot_no)).all()
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]
    order = RefillOrder(location_id=loc.id, lines_json=json.dumps(summarize(build_fill_lines(payload)), ensure_ascii=False))
    s.add(order)
    s.commit()
    s.refresh(order)
    return order


def _lane(s, slot):
    return s.scalars(select(Lane).where(Lane.slot_no == slot)).one()


def test_verify_b1_stock_up_transit_down_and_live_summary(db):
    s, loc = db
    b1 = _lane(s, "B1")
    before_stock, before_transit = b1.stock, b1.in_transit
    order = _make_order(s, loc)
    line = [l for l in json.loads(order.lines_json)["lines"] if l["slot_no"] == "B1"][0]
    assert line["fill_qty"] == 7  # 12-3-2

    snap = verify_refill(s, order.id)

    s.expire_all()
    b1 = _lane(s, "B1")
    assert b1.stock == before_stock + 7 == 10
    assert b1.in_transit == 0  # 在途2扣到0，不为负
    assert s.get(RefillOrder, order.id).status == "verified"
    # 核销按全部正补量行跳变：A1 +15、C1 +10 均满仓；B1 新缺口 = 12-10-0 = 2 仍待补
    b1_live = [l for l in snap["lines"] if l["slot_no"] == "B1"][0]
    a1_live = [l for l in snap["lines"] if l["slot_no"] == "A1"][0]
    c1_live = [l for l in snap["lines"] if l["slot_no"] == "C1"][0]
    assert b1_live["stock"] == 10 and b1_live["in_transit"] == 0 and b1_live["gap"] == 2
    assert a1_live["status"] == "full" and c1_live["status"] == "full"
    full_slots = {l["slot_no"] for l in snap["lines"] if l["status"] == "full"}
    over_slots = {l["slot_no"] for l in snap["lines"] if l["status"] == "overbooked"}
    assert "B1" not in full_slots
    assert {"A1", "A2", "B2", "C1"} <= full_slots
    assert "C2" in over_slots
    # 待补总量按新缺口：只剩 B1 的 2（核销前为 15+7+10=32）
    assert snap["total_fill"] == 2
    assert snap["need_fill_count"] == 1


def test_reverify_rejected_and_numbers_do_not_drift(db):
    s, loc = db
    order = _make_order(s, loc)
    verify_refill(s, order.id)
    stock1 = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}

    with pytest.raises(VerifyError):
        verify_refill(s, order.id)

    s.expire_all()
    stock2 = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}
    assert stock1 == stock2  # 数字不漂移
    assert s.get(RefillOrder, order.id).status == "verified"


def test_verify_void_order_rejected_three_places_untouched(db):
    s, loc = db
    order = _make_order(s, loc)
    void_refill(s, order.id)
    before = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}

    with pytest.raises(VerifyError):
        verify_refill(s, order.id)

    s.expire_all()
    after = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}
    assert before == after
    assert s.get(RefillOrder, order.id).status == "void"


def test_missing_lane_rolls_back_everything(db):
    s, loc = db
    order = _make_order(s, loc)
    # 删掉订单某行对应的货道，造成核销中途失败
    c1 = _lane(s, "C1")
    s.delete(c1)
    s.commit()
    before = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}

    with pytest.raises(VerifyError):
        verify_refill(s, order.id)

    s.expire_all()
    after = {l.slot_no: (l.stock, l.in_transit) for l in s.scalars(select(Lane)).all()}
    assert before == after  # 连排在缺失货道之前的行也不得留下半笔改动
    assert s.get(RefillOrder, order.id).status == "open"


def test_other_orders_untouched(db):
    s, loc = db
    older = _make_order(s, loc)
    older_json = older.lines_json
    order = _make_order(s, loc)
    verify_refill(s, order.id)
    # 更早的其它补货单原样保留，这次核销不改它们一个字
    assert s.get(RefillOrder, older.id).lines_json == older_json
    assert s.get(RefillOrder, older.id).status == "open"


def test_in_transit_never_negative_when_short(db):
    s, loc = db
    order = _make_order(s, loc)  # 下单时 B1 在途2、补量7
    b1 = _lane(s, "B1")
    b1.in_transit = 1  # 到货时在途只剩1：核销扣到0，不得为负
    s.commit()
    verify_refill(s, order.id)
    s.expire_all()
    b1 = _lane(s, "B1")
    assert b1.in_transit == 0
    assert b1.stock == 10  # 3 + 补量7


def test_snapshot_is_consistent_with_lanes(db):
    s, loc = db
    order = _make_order(s, loc)
    snap = live_snapshot(s, order)
    for live, lane in zip(snap["lines"], s.scalars(select(Lane).order_by(Lane.slot_no)).all()):
        assert live["stock"] == lane.stock
        assert live["in_transit"] == lane.in_transit
        assert live["gap"] == lane.capacity - lane.stock - lane.in_transit

from app.services.fill_engine import apply_arrival, build_fill_lines, compute_gap, summarize

def test_gap_basic():
    assert compute_gap(20, 5, 0) == 15
    assert compute_gap(20, 10, 5) == 5

def test_no_negative_fill():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 12, "in_transit": 0}]
    lines = build_fill_lines(lanes)
    assert lines[0].fill_qty == 0
    assert lines[0].status == "overbooked"

def test_cap_by_gap():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 20, "stock": 5, "in_transit": 0}]
    lines = build_fill_lines(lanes, requested={1: 100})
    assert lines[0].fill_qty == 15
    assert lines[0].gap == 15

def test_full_zero_fill():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 8, "in_transit": 2}]
    s = summarize(build_fill_lines(lanes))
    assert s["full_count"] == 1
    assert s["total_fill"] == 0

def test_arrival_stock_up_transit_down():
    assert apply_arrival(3, 5, 4) == (7, 1)

def test_arrival_transit_floors_at_zero():
    # 在途 2 < 补量 7：在途扣到 0 为止，不得为负
    assert apply_arrival(3, 2, 7) == (10, 0)

def test_arrival_zero_fill_keeps_lane():
    assert apply_arrival(5, 3, 0) == (5, 3)

def test_arrival_negative_fill_clamped():
    assert apply_arrival(5, 3, -2) == (5, 3)

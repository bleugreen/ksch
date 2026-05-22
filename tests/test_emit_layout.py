import re
from pathlib import Path
from typing import Any

from ksch.compiler import build_placed_project, write_project
from ksch.expand import load_project_ir
from ksch.geometry import PinPoint, symbol_rect
from ksch.kicad.sexpr import atom, load_sexpr_file
from ksch.kicad.symbols import SymbolInfo, SymbolPin, index_symbol_library
from ksch.layout import Point, Rect
from ksch.layout_problem import text_rect
from ksch.migrate import migrate_file_to_connects
from ksch.net_routing import (
    PassiveRailBank,
    PassiveRailBankMember,
    _contact_topology_route_candidates,
    _net_point_lines,
    _passive_rail_bank_lines,
    _passive_rail_bank_member_subbanks,
    _point_avoiding_obstacle_stub,
    _point_with_clear_label,
    _rail_lines,
    _safe_anchor_passive_direct_net_lines,
    _safe_contact_topology_net_lines,
    _safe_direct_net_lines,
)
from ksch.placed import PlacedItem, PlacedJunction, PlacedLabel, PlacedSymbol, PlacedWire
from ksch.placed_normalize import normalize_placed_items
from ksch.placement import (
    _is_powerish_net,
    _layout_sheet_symbols,
    _pin_by_number,
    _position_routing_risk_score,
    _symbol_body_layout_problem,
    _symbol_layout_bounds,
    _symbol_pin_point,
)
from ksch.resolver import LibraryContext, resolve_project
from ksch.routing import (
    coordinate as _coordinate,
)
from ksch.routing import (
    normalize_wire_segments as _normalize_wire_segments,
)
from ksch.routing import (
    point_on_segment as _point_on_segment,
)
from ksch.routing import (
    segments_touch as _segments_touch,
)
from ksch.validation import placed_layout_problem


def _compile_schema(tmp_path: Path, text: str) -> Path:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(text, encoding="utf-8")
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    out = tmp_path / "out"
    write_project(resolved, out, {"Test": Path("tests/fixtures/kicad/symbols/Test.kicad_sym")})
    return out / "layout_demo.kicad_sch"


def _child(expr: list[Any], token: str) -> list[Any] | None:
    for item in expr[1:]:
        if isinstance(item, list) and item and atom(item[0]) == token:
            return item
    return None


def _property_value(expr: list[Any], name: str) -> str | None:
    for item in expr[1:]:
        if (
            isinstance(item, list)
            and len(item) >= 3
            and atom(item[0]) == "property"
            and atom(item[1]) == name
        ):
            return atom(item[2])
    return None


def _symbol_positions(path: Path) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    expr = load_sexpr_file(path)
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "symbol":
            continue
        ref = _property_value(item, "Reference")
        at = _child(item, "at")
        if ref and at is not None:
            positions[ref] = (float(atom(at[1])), float(atom(at[2])))
    return positions


def _paper(path: Path) -> str:
    expr = load_sexpr_file(path)
    paper = _child(expr, "paper")
    if paper is None:
        raise AssertionError("missing paper declaration")
    return atom(paper[1])


def _placed_symbol(path: Path, ref: str) -> list[Any]:
    expr = load_sexpr_file(path)
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "symbol":
            continue
        if _child(item, "lib_id") is None:
            continue
        if _property_value(item, "Reference") == ref:
            return item
    raise AssertionError(f"missing placed symbol {ref}")


def _property_expr(symbol: list[Any], name: str) -> list[Any]:
    for item in symbol[1:]:
        if (
            isinstance(item, list)
            and len(item) >= 3
            and atom(item[0]) == "property"
            and atom(item[1]) == name
        ):
            return item
    raise AssertionError(f"missing property {name}")


def _top_level_exprs(path: Path, token: str) -> list[list[Any]]:
    expr = load_sexpr_file(path)
    return [
        item
        for item in expr[1:]
        if isinstance(item, list) and item and atom(item[0]) == token
    ]


def _named_expr(path: Path, token: str, name: str) -> list[Any]:
    for item in _top_level_exprs(path, token):
        if len(item) >= 2 and atom(item[1]) == name:
            return item
    raise AssertionError(f"missing {token} {name}")


def _expr_at(expr: list[Any]) -> tuple[float, float]:
    at = _child(expr, "at")
    if at is None:
        raise AssertionError("missing at")
    return (float(atom(at[1])), float(atom(at[2])))


def _contains_atom(expr: list[Any], value: str) -> bool:
    return any(
        atom(item) == value if not isinstance(item, list) else _contains_atom(item, value)
        for item in expr
    )


def _long_wire_count(path: Path, minimum_length: float) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if "(xy " not in line or ") (xy " not in line:
            continue
        values = [float(value) for value in re.findall(r"[-+]?[0-9]*\.?[0-9]+", line)]
        if len(values) >= 4 and (
            abs(values[0] - values[2]) >= minimum_length
            or abs(values[1] - values[3]) >= minimum_length
        ):
            count += 1
    return count


def _wire_segments(path: Path) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    expr = load_sexpr_file(path)
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "wire":
            continue
        pts = _child(item, "pts")
        if pts is None:
            continue
        coords = [
            (float(atom(xy[1])), float(atom(xy[2])))
            for xy in pts[1:]
            if isinstance(xy, list) and xy and atom(xy[0]) == "xy"
        ]
        if len(coords) == 2:
            segments.append((coords[0], coords[1]))
    return segments


def _line_wire_segments(
    items: list[PlacedItem],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [(item.start, item.end) for item in items if isinstance(item, PlacedWire)]


def _horizontal_segment_intersects_rect(
    segment: tuple[tuple[float, float], tuple[float, float]],
    rect: Rect,
) -> bool:
    start, end = segment
    if round(start[1], 2) != round(end[1], 2):
        return False
    y = start[1]
    if y < rect.top or y > rect.bottom:
        return False
    left = min(start[0], end[0])
    right = max(start[0], end[0])
    return max(left, rect.left) < min(right, rect.right)


def _wire_graph_connects(
    items: list[Any],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    graph: dict[tuple[float, float], set[tuple[float, float]]] = {}
    for item in items:
        if not isinstance(item, PlacedWire):
            continue
        item_start = _coordinate(item.start[0], item.start[1])
        item_end = _coordinate(item.end[0], item.end[1])
        graph.setdefault(item_start, set()).add(item_end)
        graph.setdefault(item_end, set()).add(item_start)

    target = _coordinate(end[0], end[1])
    frontier = [_coordinate(start[0], start[1])]
    seen: set[tuple[float, float]] = set()
    while frontier:
        current = frontier.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(graph.get(current, set()) - seen)
    return False


def _wire_path_covers_vertical_segment(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    if round(start[0], 2) != round(end[0], 2):
        return False
    expected_x = round(start[0], 2)
    required_start = round(min(start[1], end[1]), 2)
    required_end = round(max(start[1], end[1]), 2)
    intervals = sorted(
        (
            round(min(actual_start[1], actual_end[1]), 2),
            round(max(actual_start[1], actual_end[1]), 2),
        )
        for actual_start, actual_end in segments
        if round(actual_start[0], 2) == expected_x
        and round(actual_end[0], 2) == expected_x
        and round(max(actual_start[1], actual_end[1]), 2) > required_start
        and round(min(actual_start[1], actual_end[1]), 2) < required_end
    )
    cursor = required_start
    for interval_start, interval_end in intervals:
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end)
        if cursor >= required_end:
            return True
    return False


def _wire_path_covers_horizontal_segment(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    if round(start[1], 2) != round(end[1], 2):
        return False
    expected_y = round(start[1], 2)
    required_start = round(min(start[0], end[0]), 2)
    required_end = round(max(start[0], end[0]), 2)
    intervals = sorted(
        (
            round(min(actual_start[0], actual_end[0]), 2),
            round(max(actual_start[0], actual_end[0]), 2),
        )
        for actual_start, actual_end in segments
        if round(actual_start[1], 2) == expected_y
        and round(actual_end[1], 2) == expected_y
        and round(max(actual_start[0], actual_end[0]), 2) > required_start
        and round(min(actual_start[0], actual_end[0]), 2) < required_end
    )
    cursor = required_start
    for interval_start, interval_end in intervals:
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end)
        if cursor >= required_end:
            return True
    return False


def _canonical_segment(
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[tuple[float, float], tuple[float, float]]:
    start, end = segment
    return (start, end) if start <= end else (end, start)


def _visible_label_count(path: Path, name: str) -> int:
    count = 0
    expr = load_sexpr_file(path)
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "label":
            continue
        if len(item) >= 2 and atom(item[1]) == name and not _contains_atom(item, "hide"):
            count += 1
    return count


def _visible_label_names(path: Path) -> list[str]:
    expr = load_sexpr_file(path)
    names: list[str] = []
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "label":
            continue
        if len(item) >= 2 and not _contains_atom(item, "hide"):
            names.append(atom(item[1]))
    return names


def _hidden_label_count(path: Path) -> int:
    expr = load_sexpr_file(path)
    return sum(
        1
        for item in expr[1:]
        if isinstance(item, list)
        and item
        and atom(item[0]) == "label"
        and _contains_atom(item, "hide")
    )


def _visible_label_positions(path: Path, name: str) -> list[tuple[float, float]]:
    expr = load_sexpr_file(path)
    positions: list[tuple[float, float]] = []
    for item in expr[1:]:
        if (
            isinstance(item, list)
            and item
            and atom(item[0]) == "label"
            and len(item) >= 2
            and atom(item[1]) == name
            and not _contains_atom(item, "hide")
        ):
            positions.append(_expr_at(item))
    return positions


def _is_grid_aligned(value: float) -> bool:
    return abs(value / 2.54 - round(value / 2.54)) < 0.001


def test_symbol_rect_includes_graphic_body_not_only_pins() -> None:
    symbol = SymbolInfo(
        lib_id="Test:WideBody",
        name="WideBody",
        footprint=None,
        pins=[
            SymbolPin(
                name="IN",
                number="1",
                electrical_type="passive",
                at=(0.0, 0.0, 0.0),
            )
        ],
        definition=[
            "symbol",
            "WideBody",
            [
                "symbol",
                "WideBody_1_1",
                ["rectangle", ["start", -20.0, 10.0], ["end", 20.0, -10.0]],
            ],
        ],
    )

    assert symbol_rect(symbol, 100.0, 100.0) == (80.0, 90.0, 120.0, 110.0)


def test_high_fanout_rail_uses_shared_rail_labels(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  U2: {lib: Test:USBHub}
  U3: {lib: Test:USBHub}
nets:
  GND:
    - U1.GND/all
    - U2.GND/all
    - U3.GND/all
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert text.count('(label "GND"') <= 3
    assert "(junction" in text


def test_wire_segment_normalization_splits_overlapping_pin_stubs() -> None:
    normalized = _normalize_wire_segments(
        [
            (33.02, 116.84, 27.94, 116.84),
            (27.94, 116.84, 43.18, 116.84),
            (43.18, 116.84, 43.18, 82.55),
        ]
    )
    segments = {
        _canonical_segment(((start_x, start_y), (end_x, end_y)))
        for start_x, start_y, end_x, end_y in normalized
    }

    assert ((27.94, 116.84), (33.02, 116.84)) in segments
    assert ((33.02, 116.84), (43.18, 116.84)) in segments
    assert ((27.94, 116.84), (43.18, 116.84)) not in segments
    assert len(segments) == 3


def test_safe_direct_routes_do_not_leave_unlabelled_endpoint_stubs() -> None:
    end_label_point = (227.33, 160.02)
    lines = _safe_direct_net_lines(
        "Net-(U10-IN)",
        PinPoint(x=187.96, y=173.99, label_x=187.96, label_y=179.07),
        PinPoint(x=232.41, y=160.02, label_x=end_label_point[0], label_y=end_label_point[1]),
        "/power_input_5v:Net-(U10-IN)",
        page_width=420.0,
        obstacles=set(),
        occupied_segments=[],
    )
    assert lines is not None

    segments = _line_wire_segments(lines)
    endpoints = {
        (round(point[0], 2), round(point[1], 2))
        for segment in segments
        for point in segment
    }

    assert end_label_point not in endpoints
    assert (187.96, 179.07) in endpoints
    assert (232.41, 160.02) in endpoints


def test_compact_shared_node_routes_from_contact_points_before_labels() -> None:
    node_points = [
        ("U1.SW", PinPoint(x=100.0, y=100.0, label_x=105.08, label_y=100.0)),
        ("C4.2", PinPoint(x=120.32, y=92.38, label_x=125.4, label_y=92.38)),
        ("D3.K", PinPoint(x=132.08, y=100.0, label_x=137.16, label_y=100.0)),
        ("L1.1", PinPoint(x=150.0, y=100.0, label_x=155.08, label_y=100.0)),
    ]

    lines = _net_point_lines(
        "Power Input + 5V_SW",
        node_points,
        "/power_input_5v:Power Input + 5V_SW",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=False,
        allow_contact_topology_nets=True,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs={"U1"},
        small_anchor_refs=set(),
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={_coordinate(point.x, point.y) for _endpoint, point in node_points},
        occupied_segments=[],
        blocked_rects=(),
        label_blocked_rects=(),
    )

    start = (node_points[0][1].x, node_points[0][1].y)
    for _endpoint_text, point in node_points[1:]:
        assert _wire_graph_connects(lines, start, (point.x, point.y))

    labels = [item for item in lines if isinstance(item, PlacedLabel)]
    assert [(label.name, label.hidden) for label in labels] == [("SW", False)]
    assert all(
        not (
            isinstance(item, PlacedWire)
            and item.start == (node_points[0][1].label_x, node_points[0][1].label_y)
        )
        for item in lines
    )


def test_same_row_support_node_uses_one_contact_rail_label() -> None:
    node_points = [
        ("Q1.G", PinPoint(x=170.18, y=109.22, label_x=165.1, label_y=109.22)),
        ("R1.1", PinPoint(x=119.38, y=109.22, label_x=114.3, label_y=109.22)),
        ("D1.A", PinPoint(x=134.62, y=109.22, label_x=139.7, label_y=109.22)),
    ]

    lines = _net_point_lines(
        "REV_GATE",
        node_points,
        "/power_input_5v:REV_GATE",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=True,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=False,
        allow_contact_topology_nets=True,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs=set(),
        small_anchor_refs=set(),
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={_coordinate(point.x, point.y) for _endpoint, point in node_points},
        occupied_segments=[],
        blocked_rects=(),
        label_blocked_rects=(),
    )
    segments = _line_wire_segments(lines)

    assert _wire_path_covers_horizontal_segment(
        segments,
        (119.38, 109.22),
        (170.18, 109.22),
    )
    for _endpoint_text, point in node_points[1:]:
        assert _wire_graph_connects(
            lines,
            (node_points[0][1].x, node_points[0][1].y),
            (point.x, point.y),
        )
    labels = [item for item in lines if isinstance(item, PlacedLabel)]
    assert [(label.name, label.hidden) for label in labels] == [("REV_GATE", False)]


def test_non_collinear_local_node_uses_one_contact_tree_label() -> None:
    node_points = [
        ("F1.2", PinPoint(x=106.68, y=110.49, label_x=76.2, label_y=110.49)),
        ("D1.K", PinPoint(x=134.62, y=116.84, label_x=134.62, label_y=121.92)),
        ("Q1.S", PinPoint(x=177.8, y=114.3, label_x=177.8, label_y=119.38)),
        ("D2.A1", PinPoint(x=171.45, y=157.48, label_x=165.1, label_y=157.48)),
    ]

    lines = _net_point_lines(
        "Power Input + 5V_VBAT_FUSED",
        node_points,
        "/power_input_5v:Power Input + 5V_VBAT_FUSED",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=True,
        allow_safe_local_rails=True,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=True,
        allow_contact_topology_nets=True,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs=set(),
        small_anchor_refs=set(),
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={_coordinate(point.x, point.y) for _endpoint, point in node_points},
        occupied_segments=[],
        blocked_rects=(),
        label_blocked_rects=(),
    )

    start = (node_points[0][1].x, node_points[0][1].y)
    for _endpoint_text, point in node_points[1:]:
        assert _wire_graph_connects(lines, start, (point.x, point.y))
    labels = [item for item in lines if isinstance(item, PlacedLabel)]
    assert [(label.name, label.hidden) for label in labels] == [("VBAT_FUSED", False)]


def test_medium_local_contact_tree_routes_around_reserved_segments() -> None:
    node_points = [
        ("F1.2", PinPoint(x=106.68, y=110.49, label_x=76.2, label_y=110.49)),
        ("D1.K", PinPoint(x=134.62, y=116.84, label_x=134.62, label_y=121.92)),
        ("Q1.S", PinPoint(x=177.8, y=114.3, label_x=177.8, label_y=119.38)),
        ("D2.A1", PinPoint(x=171.45, y=157.48, label_x=165.1, label_y=157.48)),
    ]
    reserved = [
        (152.4, 121.92, 152.4, 127.0),
        (111.76, 153.67, 226.06, 153.67),
    ]

    lines = _net_point_lines(
        "Power Input + 5V_VBAT_FUSED",
        node_points,
        "/power_input_5v:Power Input + 5V_VBAT_FUSED",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=True,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=True,
        allow_contact_topology_nets=True,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs=set(),
        small_anchor_refs=set(),
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={_coordinate(point.x, point.y) for _endpoint, point in node_points},
        occupied_segments=reserved.copy(),
        blocked_rects=(),
        label_blocked_rects=(),
    )

    start = (node_points[0][1].x, node_points[0][1].y)
    for _endpoint_text, point in node_points[1:]:
        assert _wire_graph_connects(lines, start, (point.x, point.y))
    labels = [item for item in lines if isinstance(item, PlacedLabel)]
    assert [(label.name, label.hidden) for label in labels] == [("VBAT_FUSED", False)]
    for wire in [item for item in lines if isinstance(item, PlacedWire)]:
        segment = (wire.start[0], wire.start[1], wire.end[0], wire.end[1])
        assert not any(_segments_touch(segment, reserved_segment) for reserved_segment in reserved)


def test_endpoint_labels_avoid_symbol_body_blockers() -> None:
    symbol_body = Rect(left=104.14, top=104.14, right=109.22, bottom=109.22)
    lines = _net_point_lines(
        "VBAT_FUSED",
        [("F1.2", PinPoint(x=106.68, y=110.49, label_x=106.68, label_y=106.68))],
        "/power_input_5v:VBAT_FUSED",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=False,
        allow_contact_topology_nets=False,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs=set(),
        small_anchor_refs=set(),
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles=set(),
        occupied_segments=[],
        blocked_rects=(),
        label_blocked_rects=(symbol_body,),
    )

    label = next(item for item in lines if isinstance(item, PlacedLabel))
    assert not text_rect(Point(*label.at), label.name, justify=label.justify).overlaps(
        symbol_body
    )


def test_contact_topology_labels_justify_away_from_parent_anchor() -> None:
    points = [
        ("F3.2", PinPoint(x=111.76, y=153.67, label_x=106.68, label_y=153.67)),
        ("U10.VS@8", PinPoint(x=232.41, y=154.94, label_x=226.06, label_y=154.94)),
        ("U10.VS@9", PinPoint(x=232.41, y=154.94, label_x=226.06, label_y=154.94)),
    ]

    candidates = _contact_topology_route_candidates(
        points,
        page_width=420.0,
        anchor_label_xs=(243.84,),
    )

    assert any(
        label_x < 243.84 and label_x > 190.0 and justify == "right"
        for _segments, (label_x, _label_y), justify, _split_points in candidates
    )


def test_contact_topology_labels_sit_off_route_spines() -> None:
    points = [
        ("F3.2", PinPoint(x=111.76, y=153.67, label_x=106.68, label_y=153.67)),
        ("U10.VS@8", PinPoint(x=232.41, y=154.94, label_x=226.06, label_y=154.94)),
        ("U10.VS@9", PinPoint(x=232.41, y=154.94, label_x=226.06, label_y=154.94)),
    ]

    lines = _safe_contact_topology_net_lines(
        "SCREEN_12V_FUSED",
        points,
        "/power_input_5v:Power Input + 5V_SCREEN_12V_FUSED",
        page_width=420.0,
        obstacles={_coordinate(point.x, point.y) for _endpoint, point in points},
        occupied_segments=[],
        blocked_rects=(),
        label_blocked_rects=(),
        anchor_label_xs=(243.84,),
    )

    assert lines is not None
    label = next(item for item in lines if isinstance(item, PlacedLabel))
    label_rect = text_rect(Point(*label.at), label.name, justify=label.justify)
    assert not any(
        _horizontal_segment_intersects_rect(segment, label_rect)
        for segment in _line_wire_segments(lines)
    )


def test_label_chooser_prefers_clear_opposite_side_before_same_axis_overlap() -> None:
    horizontal_blocker = Rect(left=109.22, top=109.22, right=160.02, bottom=111.76)
    point = _point_with_clear_label(
        "VBAT_FUSED",
        PinPoint(x=106.68, y=110.49, label_x=111.76, label_y=110.49),
        page_width=420.0,
        blocked_rects=(horizontal_blocker,),
        stub_blocked_rects=(),
        obstacles=set(),
        occupied_segments=[],
    )

    label_rect = text_rect(
        Point(point.label_x, point.label_y),
        "VBAT_FUSED",
        justify="right" if point.label_x < point.x else "left",
    )
    assert point.label_x < 106.68
    assert not label_rect.overlaps(horizontal_blocker)


def test_divider_tap_uses_stack_topology_before_generic_contact_topology() -> None:
    tap_points = [
        ("U1.FB", PinPoint(x=261.62, y=114.3, label_x=256.54, label_y=114.3)),
        ("R5.2", PinPoint(x=327.66, y=125.73, label_x=332.74, label_y=125.73)),
        ("R6.1", PinPoint(x=327.66, y=133.35, label_x=332.74, label_y=133.35)),
    ]

    lines = _net_point_lines(
        "Power Input + 5V_FB",
        tap_points,
        "/power_input_5v:Power Input + 5V_FB",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=True,
        allow_contact_topology_nets=True,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs={"U1"},
        small_anchor_refs=set(),
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={_coordinate(point.x, point.y) for _endpoint, point in tap_points},
        occupied_segments=[],
        blocked_rects=(),
        label_blocked_rects=(),
    )
    segments = _line_wire_segments(lines)

    assert _wire_path_covers_vertical_segment(
        segments,
        (327.66, 125.73),
        (327.66, 133.35),
    )
    assert not _wire_path_covers_vertical_segment(
        segments,
        (332.74, 118.11),
        (332.74, 133.35),
    )
    assert _wire_graph_connects(lines, (261.62, 114.3), (327.66, 125.73))
    assert _wire_graph_connects(lines, (261.62, 114.3), (327.66, 133.35))


def test_generated_net_labels_are_never_hidden(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  C1: {lib: Test:C, value: 100nF}
  C2: {lib: Test:C, value: 1uF}
power_flags:
  - VBUS
nets:
  VBUS:
    - U1.VBUS_DET
    - C1.1
    - C2.1
  GND:
    - U1.GND/all
    - C1.2
    - C2.2
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert "(label " in text
    assert _hidden_label_count(schematic) == 0
    assert "(size 0.01 0.01)" not in text


def test_placed_normalization_deduplicates_exact_visible_labels() -> None:
    first = PlacedLabel(
        name="SCREEN_12V_OUT",
        at=(260.35, 154.94),
        uuid="first",
        justify="left",
        nets=frozenset({"SCREEN_12V_OUT"}),
    )
    duplicate = PlacedLabel(
        name="SCREEN_12V_OUT",
        at=(260.35, 154.94),
        uuid="duplicate",
        justify="left",
        nets=frozenset({"SCREEN_12V_OUT"}),
    )
    distinct = PlacedLabel(
        name="SCREEN_12V_OUT",
        at=(260.35, 157.48),
        uuid="distinct",
        justify="left",
        nets=frozenset({"SCREEN_12V_OUT"}),
    )

    normalized = normalize_placed_items((first, duplicate, distinct))

    assert normalized == (first, distinct)


def test_passives_cluster_near_connected_anchor(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 9)
    )
    capacitor_tops = "\n".join(f"    - C{index}.1" for index in range(1, 9))
    capacitor_nets = "\n".join(f"  GND_C{index}:\n    - C{index}.2\n" for index in range(1, 9))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
nets:
  DECOUPLE:
    - U1.VBUS_DET
{capacitor_tops}
{capacitor_nets}
""",
    )

    positions = _symbol_positions(schematic)
    anchor_y = positions["U1"][1]

    assert max(abs(positions[f"C{index}"][1] - anchor_y) for index in range(1, 9)) <= 90


def test_symbol_placement_stays_inside_paper_width(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 19)
    )
    capacitor_nets = "\n".join(f"    - C{index}.1" for index in range(1, 19))
    a4_schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
nets:
  DECOUPLE:
    - U1.VBUS_DET
{capacitor_nets}
""",
    )

    a4_positions = _symbol_positions(a4_schematic)
    assert _paper(a4_schematic) == "A4"
    assert max(x for x, _y in a4_positions.values()) <= 297.0 - 38.1

    extra_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(19, 23)
    )
    extra_nets = "\n".join(f"    - C{index}.1" for index in range(19, 23))
    a3_schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
{extra_symbols}
nets:
  DECOUPLE:
    - U1.VBUS_DET
{capacitor_nets}
{extra_nets}
""",
    )

    a3_positions = _symbol_positions(a3_schematic)
    assert _paper(a3_schematic) == "A3"
    assert max(x for x, _y in a3_positions.values()) <= 420.0 - 38.1


def test_wide_symbol_geometry_stays_inside_layout_bounds(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:HugeController}
nets:
  GND:
    - U1.GND
""",
    )
    project = load_project_ir(tmp_path / "project.ksch.yaml")
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    positions = {
        (ref, 1): Point(x=x, y=y)
        for ref, (x, y) in _symbol_positions(schematic).items()
        if ref == "U1"
    }
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(resolved, "/")
    element = _symbol_body_layout_problem(resolved, "/", positions).elements[0]

    assert min_x <= element.rect.left
    assert element.rect.right <= max_x
    assert min_y <= element.rect.top
    assert element.rect.bottom <= max_y


def test_symbol_sheets_do_not_route_hierarchy_labels_across_page(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 13)
    )
    vin_endpoints = "\n".join(f"    - C{index}.1" for index in range(1, 13))
    gnd_endpoints = "\n".join(f"    - C{index}.2" for index in range(1, 13))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  GND: power_in
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
nets:
  VIN:
    - U1.VBUS_DET
{vin_endpoints}
  GND:
    - U1.GND/all
{gnd_endpoints}
""",
    )

    assert _long_wire_count(schematic, 120) == 0


def test_low_interface_local_circuits_use_functional_flow_layout(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  VOUT: power_out
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  Q1: {lib: Test:C}
  U1: {lib: Test:USBHub}
  L1: {lib: Test:C}
  C1: {lib: Test:C, value: 100nF}
  C2: {lib: Test:C, value: 100nF}
  R1: {lib: Test:C, value: 10k}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  LOCAL_PROT:
    - F1.2
    - Q1.1
    - U1.VBUS_DET
    - C1.1
  LOCAL_SW:
    - U1.USBDP_UP
    - L1.1
    - C2.1
  LOCAL_FB:
    - U1.USBDM_UP
    - R1.1
  VOUT:
    - L1.2
  GND:
    - U1.GND/all
    - J1.D+@A6
    - Q1.2
    - C1.2
    - C2.2
    - R1.2
""",
    )

    positions = _symbol_positions(schematic)

    assert positions["J1"][0] < positions["F1"][0] < positions["Q1"][0]
    assert positions["Q1"][0] < positions["U1"][0] < positions["L1"][0]
    assert positions["U1"][0] >= 190


def test_low_interface_layout_resolves_symbol_body_overlaps(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  U1: {lib: Test:WideController}
  C1: {lib: Test:C, value: 100nF}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  Power Input + 5V_BUCK_EN:
    - U1.EN
    - C1.1
  GND:
    - U1.GND
    - C1.2
""",
    )
    project = load_project_ir(tmp_path / "project.ksch.yaml")
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    positions = {
        (ref, 1): Point(x=x, y=y)
        for ref, (x, y) in _symbol_positions(schematic).items()
        if ref in {"U1", "C1"}
    }

    assert _symbol_body_layout_problem(resolved, "/", positions).overlaps() == ()


def test_local_circuit_body_overlap_resolution_allows_five_interface_ports(
    tmp_path: Path,
) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  VOUT: power_out
  GND: power_in
  EN: input
  MODE: input
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  U1: {lib: Test:WideController}
  C1: {lib: Test:C, value: 100nF}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  Power Input + 5V_BUCK_EN:
    - U1.EN
    - C1.1
  GND:
    - U1.GND
    - C1.2
""",
    )
    project = load_project_ir(tmp_path / "project.ksch.yaml")
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    positions = {
        (ref, 1): Point(x=x, y=y)
        for ref, (x, y) in _symbol_positions(schematic).items()
        if ref in {"U1", "C1"}
    }

    assert _symbol_body_layout_problem(resolved, "/", positions).overlaps() == ()


def test_symbol_reference_and_value_fields_clear_symbol_body(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
nets:
  GND:
    - U1.GND/all
""",
    )

    symbol = _placed_symbol(schematic, "U1")
    symbol_at = _child(symbol, "at")
    reference_at = _child(_property_expr(symbol, "Reference"), "at")
    value_at = _child(_property_expr(symbol, "Value"), "at")
    assert symbol_at is not None
    assert reference_at is not None
    assert value_at is not None
    symbol_y = float(atom(symbol_at[2]))

    assert float(atom(reference_at[2])) < symbol_y - 10
    assert float(atom(value_at[2])) > symbol_y + 10


def test_vertical_passive_fields_sit_beside_the_symbol(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  R1: {lib: Test:C, value: 10k}
nets:
  GND:
    - R1.2
""",
    )

    symbol = _placed_symbol(schematic, "R1")
    symbol_at = _child(symbol, "at")
    reference = _property_expr(symbol, "Reference")
    value = _property_expr(symbol, "Value")
    reference_at = _child(reference, "at")
    value_at = _child(value, "at")
    assert symbol_at is not None
    assert reference_at is not None
    assert value_at is not None
    symbol_x = float(atom(symbol_at[1]))
    symbol_y = float(atom(symbol_at[2]))

    assert float(atom(reference_at[1])) < symbol_x
    assert float(atom(value_at[1])) < symbol_x
    assert _contains_atom(reference, "right")
    assert _contains_atom(value, "right")
    assert abs(float(atom(reference_at[2])) - symbol_y) <= 3
    assert abs(float(atom(value_at[2])) - symbol_y) <= 3


def test_vertical_passive_fields_clear_visible_pin_labels(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  R1: {lib: Test:C, value: 10k}
nets:
  TOP_NODE:
    - R1.1
  GND:
    - R1.2
""",
    )

    symbol = _placed_symbol(schematic, "R1")
    field_rects = []
    for field_name in ("Reference", "Value"):
        property_ = _property_expr(symbol, field_name)
        at = _child(property_, "at")
        assert at is not None
        justify = "right" if _contains_atom(property_, "right") else "left"
        field_rects.append(
            text_rect(
                Point(float(atom(at[1])), float(atom(at[2]))),
                atom(property_[2]),
                justify=justify,
            )
        )

    label_rects = [
        text_rect(Point(x, y), label)
        for label in ("TOP_NODE", "GND")
        for x, y in _visible_label_positions(schematic, label)
    ]

    assert field_rects
    assert label_rects
    assert not any(field.overlaps(label) for field in field_rects for label in label_rects)


def test_vertical_passive_net_labels_anchor_signals_beside_leads_and_gnd_down(
    tmp_path: Path,
) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  C1: {lib: Test:C, value: 1uF}
nets:
  HUB_RST_N:
    - C1.1
  GND:
    - C1.2
""",
    )

    positions = _symbol_positions(schematic)
    segments = {_canonical_segment(segment) for segment in _wire_segments(schematic)}
    c1_x, c1_y = positions["C1"]
    top_pin = (c1_x, c1_y - 3.81)
    bottom_pin = (c1_x, c1_y + 3.81)
    top_label = (c1_x + 5.08, top_pin[1])
    bottom_labels = _visible_label_positions(schematic, "GND")

    assert _visible_label_positions(schematic, "HUB_RST_N") == [top_label]
    assert len(bottom_labels) == 1
    bottom_label = bottom_labels[0]
    assert bottom_label[0] == c1_x
    assert bottom_label[1] > bottom_pin[1]
    assert _canonical_segment((top_pin, top_label)) in segments
    assert _canonical_segment((bottom_pin, bottom_label)) in segments


def test_low_interface_local_circuits_route_two_pin_local_nodes_once(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  VOUT: power_out
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  Q1: {lib: Test:C}
  U1: {lib: Test:USBHub}
  L1: {lib: Test:C}
  C1: {lib: Test:C, value: 100nF}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  VOUT:
    - L1.1
  GND:
    - U1.GND/all
  Power Input + 5V_BOOT:
    - U1.USBDP_UP
    - C1.1
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert _visible_label_count(schematic, "BOOT") == 1
    assert text.count('(label "BOOT"') == 1
    assert _hidden_label_count(schematic) == 0
    assert "Power Input + 5V_BOOT" not in text
    assert text.count("(wire") >= 4


def test_passive_continuation_places_and_routes_shunt_near_anchor_passive(
    tmp_path: Path,
) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 100k}
  C1: {lib: Test:C, value: 10nF}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  LOCAL_PROT:
    - F1.2
    - U1.VBUS_DET
  Power Input + 5V_COMP:
    - U1.USBDP_UP
    - R1.2
  Power Input + 5V_COMP_RC:
    - R1.1
    - C1.2
  GND:
    - U1.GND/all
    - J1.D+@A6
    - C1.1
""",
    )

    positions = _symbol_positions(schematic)
    segments = _wire_segments(schematic)
    u1_x, _u1_y = positions["U1"]
    r1_x, r1_y = positions["R1"]
    c1_x, c1_y = positions["C1"]
    c1_at = _child(_placed_symbol(schematic, "C1"), "at")
    r1_at = _child(_placed_symbol(schematic, "R1"), "at")
    assert c1_at is not None
    assert r1_at is not None
    c1_rotation = int(float(atom(c1_at[3]))) if len(c1_at) > 3 else 0
    r1_rotation = int(float(atom(r1_at[3]))) if len(r1_at) > 3 else 0
    test_symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    passive_info = test_symbols.symbols["Test:C"]
    r1_pin = _pin_by_number(passive_info, "1")
    c1_pin = _pin_by_number(passive_info, "2")
    assert r1_pin is not None
    assert c1_pin is not None
    r1_continuation_point = _symbol_pin_point(
        r1_x,
        r1_y,
        r1_pin,
        symbol_info=passive_info,
        symbol_rotation=r1_rotation,
    )
    c1_continuation_point = _symbol_pin_point(
        c1_x,
        c1_y,
        c1_pin,
        symbol_info=passive_info,
        symbol_rotation=c1_rotation,
    )
    r1_continuation_pin = (r1_continuation_point.x, r1_continuation_point.y)
    c1_continuation_pin = (c1_continuation_point.x, c1_continuation_point.y)

    assert abs(c1_x - r1_x) <= 38.1
    assert c1_y > r1_y
    assert abs(c1_x - u1_x) >= abs(r1_x - u1_x)
    assert _wire_graph_connects(
        [
            PlacedWire(start=start, end=end, uuid=f"segment-{index}")
            for index, (start, end) in enumerate(segments)
        ],
        r1_continuation_pin,
        c1_continuation_pin,
    )
    assert _visible_label_count(schematic, "COMP_RC") == 1
    assert "Power Input + 5V_COMP_RC" not in schematic.read_text(encoding="utf-8")


def test_multi_branch_anchor_support_aligns_branch_nodes_on_anchor_row(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 100k}
  D1: {lib: Test:H2, value: clamp}
nets:
  REV_GATE:
    - U1.USBDP_UP
    - R1.1
    - D1.A
  GND:
    - R1.2
  SOURCE:
    - D1.K
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    placed = build_placed_project(resolved)
    sheet = placed.sheets[0]
    placed_symbols = {
        item.reference: item for item in sheet.items if isinstance(item, PlacedSymbol)
    }
    hub_info = resolved.symbol_library["Test:USBHub"]
    passive_info = resolved.symbol_library["Test:C"]
    clamp_info = resolved.symbol_library["Test:H2"]
    anchor_pin = _pin_by_number(hub_info, "1")
    r1_top_pin = _pin_by_number(passive_info, "1")
    r1_bottom_pin = _pin_by_number(passive_info, "2")
    d1_return_pin = _pin_by_number(clamp_info, "1")
    d1_anchor_pin = _pin_by_number(clamp_info, "2")
    assert anchor_pin is not None
    assert r1_top_pin is not None
    assert r1_bottom_pin is not None
    assert d1_return_pin is not None
    assert d1_anchor_pin is not None

    anchor_point = _symbol_pin_point(
        placed_symbols["U1"].at[0],
        placed_symbols["U1"].at[1],
        anchor_pin,
        symbol_info=hub_info,
        symbol_rotation=placed_symbols["U1"].rotation,
    )
    r1_top_point = _symbol_pin_point(
        placed_symbols["R1"].at[0],
        placed_symbols["R1"].at[1],
        r1_top_pin,
        symbol_info=passive_info,
        symbol_rotation=placed_symbols["R1"].rotation,
    )
    r1_bottom_point = _symbol_pin_point(
        placed_symbols["R1"].at[0],
        placed_symbols["R1"].at[1],
        r1_bottom_pin,
        symbol_info=passive_info,
        symbol_rotation=placed_symbols["R1"].rotation,
    )
    d1_anchor_point = _symbol_pin_point(
        placed_symbols["D1"].at[0],
        placed_symbols["D1"].at[1],
        d1_anchor_pin,
        symbol_info=clamp_info,
        symbol_rotation=placed_symbols["D1"].rotation,
    )
    d1_return_point = _symbol_pin_point(
        placed_symbols["D1"].at[0],
        placed_symbols["D1"].at[1],
        d1_return_pin,
        symbol_info=clamp_info,
        symbol_rotation=placed_symbols["D1"].rotation,
    )

    assert placed_symbols["D1"].rotation == 90
    assert round(r1_top_point.y, 2) == round(anchor_point.y, 2)
    assert round(d1_anchor_point.y, 2) == round(anchor_point.y, 2)
    assert r1_bottom_point.y > r1_top_point.y
    assert d1_return_point.y > d1_anchor_point.y
    assert abs(r1_top_point.x - d1_anchor_point.x) >= 12.7


def test_feedback_divider_uses_single_visible_tap_label(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  VOUT: power_out
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  Q1: {lib: Test:C}
  U1: {lib: Test:USBHub}
  L1: {lib: Test:C}
  R5: {lib: Test:C, value: 31.6k 1%}
  R6: {lib: Test:C, value: 10.0k 1%}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  VOUT:
    - L1.1
    - R5.1
  GND:
    - U1.GND/all
    - R6.2
  Power Input + 5V_FB:
    - U1.USBDP_UP
    - R5.2
    - R6.1
""",
    )

    text = schematic.read_text(encoding="utf-8")
    positions = _symbol_positions(schematic)
    segments = _wire_segments(schematic)
    r5_x, r5_y = positions["R5"]
    r6_x, r6_y = positions["R6"]
    r5_bottom_pin = (r5_x, r5_y + 3.81)
    r6_top_pin = (r6_x, r6_y - 3.81)
    tap_y = round(round(((r5_bottom_pin[1] + r6_top_pin[1]) / 2) / 2.54) * 2.54, 2)

    assert _visible_label_count(schematic, "FB") == 1
    assert "Power Input + 5V_FB" not in text
    assert "(junction" in text
    assert _wire_path_covers_vertical_segment(segments, r5_bottom_pin, r6_top_pin)
    assert any(
        round(start[1], 2) == tap_y
        and round(end[1], 2) == tap_y
        and (round(start[0], 2) == round(r5_x, 2) or round(end[0], 2) == round(r5_x, 2))
        and round(start[0], 2) != round(end[0], 2)
        for start, end in segments
    )


def test_repeated_output_caps_use_shared_power_and_ground_rails(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  VOUT: power_out
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  Q1: {lib: Test:C}
  U1: {lib: Test:USBHub}
  L1: {lib: Test:C}
  C5: {lib: Test:C, value: 47uF 10V}
  C6: {lib: Test:C, value: 47uF 10V}
  C7: {lib: Test:C, value: 100nF 10V}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  VOUT:
    - L1.1
    - C5.1
    - C6.1
    - C7.1
  GND:
    - U1.GND/all
    - C5.2
    - C6.2
    - C7.2
""",
    )

    positions = _symbol_positions(schematic)
    segments = _wire_segments(schematic)
    cap_refs = ["C5", "C6", "C7"]
    cap_positions = [positions[ref] for ref in cap_refs]
    top_pins = [(x, y - 3.81) for x, y in cap_positions]
    bottom_pins = [(x, y + 3.81) for x, y in cap_positions]

    assert len({round(y, 2) for _x, y in cap_positions}) == 1
    assert [x for x, _y in cap_positions] == sorted(x for x, _y in cap_positions)
    assert _visible_label_count(schematic, "VOUT") == 1
    assert _visible_label_count(schematic, "GND") <= 2
    assert _wire_path_covers_horizontal_segment(segments, top_pins[0], top_pins[-1])
    assert _wire_path_covers_horizontal_segment(segments, bottom_pins[0], bottom_pins[-1])


def _shared_vertical_rail_x(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    points: list[tuple[float, float]],
) -> float | None:
    min_y = min(y for _x, y in points)
    max_y = max(y for _x, y in points)
    rail_xs = sorted(
        {
            round(start[0], 2)
            for start, end in segments
            if round(start[0], 2) == round(end[0], 2)
            and _wire_path_covers_vertical_segment(segments, (start[0], min_y), (end[0], max_y))
        }
    )
    for rail_x in rail_xs:
        if all(
            _wire_path_covers_horizontal_segment(segments, point, (rail_x, point[1]))
            for point in points
        ):
            return rail_x
    return None


def test_large_sheet_repeated_caps_use_shared_side_rails(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 36)
    )
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
nets:
  HUB_3V3:
    - U1.VBUS_DET
    - C1.1
    - C2.1
    - C3.1
    - C4.1
  GND:
    - U1.GND/all
    - C1.2
    - C2.2
    - C3.2
    - C4.2
""",
    )

    positions = _symbol_positions(schematic)
    segments = _wire_segments(schematic)
    cap_refs = ["C1", "C2", "C3", "C4"]
    cap_positions = [positions[ref] for ref in cap_refs]
    top_pins = [(x, y - 3.81) for x, y in cap_positions]
    bottom_pins = [(x, y + 3.81) for x, y in cap_positions]

    assert len({round(y, 2) for _x, y in cap_positions}) > 1
    assert _visible_label_count(schematic, "HUB_3V3") >= 1
    assert _hidden_label_count(schematic) == 0
    assert _shared_vertical_rail_x(segments, top_pins) is not None
    assert _shared_vertical_rail_x(segments, bottom_pins) is not None


def test_large_sheet_support_passives_avoid_ic_pin_label_field(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 36)
    )
    top_endpoints = "\n".join(f"    - C{index}.1" for index in range(1, 9))
    bottom_endpoints = "\n".join(f"    - C{index}.2" for index in range(1, 9))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
nets:
  VDD:
    - U1.VBUS_DET
{top_endpoints}
  GND:
    - U1.GND/all
{bottom_endpoints}
""",
    )

    positions = _symbol_positions(schematic)
    ic_x, ic_y = positions["U1"]
    support_positions = [positions[f"C{index}"] for index in range(1, 9)]

    assert not any(
        ic_x - 5.08 <= x <= ic_x + 76.2 and abs(y - ic_y) <= 35.56
        for x, y in support_positions
    )


def test_large_functional_sheet_places_power_island_before_controller(
    tmp_path: Path,
) -> None:
    filler_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: filler}}" for index in range(10, 45)
    )
    filler_nets = "\n".join(f"  FILL_{index}:\n    - C{index}.1\n" for index in range(10, 45))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  J1: {{lib: Test:USB_C}}
  F1: {{lib: Test:C, value: fuse}}
  U1: {{lib: Test:USBHub, value: downstream controller}}
  U2: {{lib: Test:BaseSwitch, value: buck regulator}}
  L1: {{lib: Test:C, value: buck inductor}}
  C1: {{lib: Test:C, value: input cap}}
  C2: {{lib: Test:C, value: output cap}}
  C3: {{lib: Test:C, value: output cap}}
  R1: {{lib: Test:C, value: feedback}}
{filler_symbols}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
    - U2.GND
    - C1.1
  SW:
    - U2.OUT
    - L1.1
  VOUT:
    - L1.2
    - C2.1
    - C3.1
    - U1.VBUS_DET
    - R1.1
  GND:
    - U1.GND/all
    - C1.2
    - C2.2
    - C3.2
    - R1.2
{filler_nets}
""",
    )

    positions = _symbol_positions(schematic)
    switcher_x, switcher_y = positions["U2"]
    controller_x, _controller_y = positions["U1"]
    output_cap_positions = [positions["C2"], positions["C3"]]

    assert switcher_x < controller_x - 50.8
    assert max(abs(x - switcher_x) for x, _y in output_cap_positions) <= 80.0
    assert max(abs(y - switcher_y) for _x, y in output_cap_positions) <= 80.0


def test_large_functional_sheet_keeps_interface_filter_path_before_controller(
    tmp_path: Path,
) -> None:
    filler_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: filler}}" for index in range(10, 45)
    )
    filler_nets = "\n".join(f"  FILL_{index}:\n    - C{index}.1\n" for index in range(10, 45))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  J1: {{lib: Test:USB_C, value: coax connector}}
  F1: {{lib: Test:C, value: PoC fuse}}
  L1: {{lib: Test:C, value: PoC inductor}}
  U1: {{lib: Test:USBHub, value: deserializer}}
  C1: {{lib: Test:C, value: local AC cap}}
{filler_symbols}
nets:
  VBAT_IN:
    - J1.VBUS/all
    - F1.1
  POC_STAGE:
    - F1.2
    - L1.1
  CAM_COAX_CENTER:
    - L1.2
    - U1.VBUS_DET
    - C1.1
  GND:
    - U1.GND/all
    - C1.2
{filler_nets}
""",
    )

    positions = _symbol_positions(schematic)

    assert positions["J1"][0] < positions["F1"][0] < positions["L1"][0]
    assert positions["L1"][0] < positions["U1"][0]
    assert abs(positions["L1"][1] - positions["U1"][1]) <= 90.0


def test_large_functional_sheet_compacts_generic_connected_two_pin_chain(
    tmp_path: Path,
) -> None:
    filler_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: filler}}" for index in range(10, 45)
    )
    filler_nets = "\n".join(f"  FILL_{index}:\n    - C{index}.1\n" for index in range(10, 45))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
interface:
  SOURCE: power_in
  FILTER_OUT: passive
  GND: power_in
symbols:
  J1: {{lib: Test:USB_C, value: source connector}}
  F1: {{lib: Test:C, value: input fuse}}
  L1: {{lib: Test:C, value: stage inductor}}
  L2: {{lib: Test:C, value: stage inductor}}
  L3: {{lib: Test:C, value: stage inductor}}
  L4: {{lib: Test:C, value: stage inductor}}
  R1: {{lib: Test:C, value: damping resistor}}
  R2: {{lib: Test:C, value: damping resistor}}
  U1: {{lib: Test:USBHub, value: load controller}}
{filler_symbols}
nets:
  SOURCE:
    - J1.VBUS/all
    - F1.1
  FILTER_STAGE_A:
    - F1.2
    - L1.1
  FILTER_STAGE_B:
    - L1.2
    - L2.1
    - R1.1
  FILTER_STAGE_C:
    - L2.2
    - L3.1
    - R1.2
    - R2.1
  FILTER_STAGE_D:
    - L3.2
    - L4.1
    - R2.2
  FILTER_OUT:
    - L4.2
    - U1.VBUS_DET
  GND:
    - U1.GND/all
{filler_nets}
""",
    )

    positions = _symbol_positions(schematic)
    chain_positions = [positions[ref] for ref in ("F1", "L1", "L2", "L3", "L4")]
    chain_y_span = max(y for _x, y in chain_positions) - min(y for _x, y in chain_positions)

    assert positions["J1"][0] < min(positions[ref][0] for ref in ("F1", "L1", "L2", "L3", "L4"))
    assert max(positions[ref][0] for ref in ("F1", "L1", "L2", "L3", "L4")) < positions["U1"][0]
    assert (
        positions["F1"][0]
        < positions["L1"][0]
        < positions["L2"][0]
        < positions["L3"][0]
        < positions["L4"][0]
    )
    assert chain_y_span <= 60.0
    assert max(abs(positions[ref][1] - positions["R1"][1]) for ref in ("L2", "L3")) <= 35.56
    assert max(abs(positions[ref][1] - positions["R2"][1]) for ref in ("L3", "L4")) <= 35.56


def test_position_routing_risk_scores_cross_net_candidate_contacts(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: risk_demo
symbols:
  C1: {lib: Test:C}
  C2: {lib: Test:C}
  C3: {lib: Test:C}
  C4: {lib: Test:C}
nets:
  A:
    - C1.1
    - C2.1
  B:
    - C3.1
    - C4.1
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))

    crossing = {
        ("C1", 1): Point(50.8, 50.8),
        ("C2", 1): Point(101.6, 101.6),
        ("C3", 1): Point(101.6, 50.8),
        ("C4", 1): Point(50.8, 101.6),
    }
    separated = {
        ("C1", 1): Point(50.8, 50.8),
        ("C2", 1): Point(101.6, 50.8),
        ("C3", 1): Point(50.8, 101.6),
        ("C4", 1): Point(101.6, 101.6),
    }

    assert _position_routing_risk_score(resolved, "/", crossing) > _position_routing_risk_score(
        resolved,
        "/",
        separated,
    )


def test_sheet_local_labels_strip_imported_prefix(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  U2: {lib: Test:USBHub}
nets:
  USB Hub + Ports_USB_SERVICE_DN:
    - U1.USBDM_UP
  USB Hub + Ports_USB_SERVICE_DP:
    - U1.USBDP_UP
  USB Hub + Ports_PRTPWR1:
    - U2.USBDM_UP
  USB Hub + Ports_OCS_N1:
    - U2.USBDP_UP
""",
    )

    visible_labels = _visible_label_names(schematic)

    assert "USB Hub + Ports_USB_SERVICE_DN" not in visible_labels
    assert "USB Hub + Ports_USB_SERVICE_DP" not in visible_labels
    assert "USB_SERVICE_DN" in visible_labels
    assert "USB_SERVICE_DP" in visible_labels
    assert "PRTPWR1" in visible_labels
    assert "OCS_N1" in visible_labels


def test_large_sheet_prefers_visible_labels_away_from_controller_pins(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 35)
    )
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:DenseController}}
{capacitor_symbols}
nets:
  USB Hub + Ports_USB_SERVICE_DN:
    - U1.USBDM_UP
    - C1.1
  USB Hub + Ports_USB_SERVICE_DP:
    - U1.USBDP_UP
    - C2.1
  USB Hub + Ports_VBUS_DET:
    - U1.VBUS_DET
    - C3.1
  GND:
    - U1.GND/all
    - C1.2
    - C2.2
    - C3.2
""",
    )

    positions = _symbol_positions(schematic)
    u1_x, _u1_y = positions["U1"]
    label_positions = _visible_label_positions(schematic, "USB_SERVICE_DN")

    assert len(label_positions) == 2
    assert any(label_x > u1_x + 35.56 for label_x, _label_y in label_positions)
    assert _hidden_label_count(schematic) == 0


def test_small_u_prefix_parts_keep_visible_pin_labels(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U4: {lib: Test:BaseSwitch}
  R1: {lib: Test:C, value: 10k}
nets:
  LOAD_EN:
    - U4.OUT
    - R1.1
  GND:
    - U4.GND
    - R1.2
""",
    )

    positions = _symbol_positions(schematic)
    label_positions = _visible_label_positions(schematic, "LOAD_EN")

    assert label_positions
    assert any(
        abs(label_x - positions["U4"][0]) < abs(label_x - positions["R1"][0])
        for label_x, _label_y in label_positions
    )


def test_shared_small_u_rails_keep_visible_labels_at_each_small_part(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:BaseSwitch}
  U4: {lib: Test:BaseSwitch}
  C1: {lib: Test:C, value: 100nF}
nets:
  LOAD_BUS:
    - U1.OUT
    - U4.OUT
    - C1.1
  GND:
    - U1.GND
    - U4.GND
    - C1.2
""",
    )

    positions = _symbol_positions(schematic)
    label_positions = _visible_label_positions(schematic, "LOAD_BUS")

    assert len(label_positions) >= 2
    assert any(
        abs(label_x - positions["U4"][0]) < abs(label_x - positions["C1"][0])
        and abs(label_y - positions["U4"][1]) <= 10
        for label_x, label_y in label_positions
    )


def test_blocked_passive_bank_can_emit_clear_member_subset() -> None:
    def member(ref: str, y: float) -> PassiveRailBankMember:
        return PassiveRailBankMember(
            ref=ref,
            top_endpoint=f"{ref}.1",
            bottom_endpoint=f"{ref}.2",
            top_point=PinPoint(x=100.0, y=y, label_x=100.0, label_y=y),
            bottom_point=PinPoint(x=100.0, y=y + 7.62, label_x=100.0, label_y=y + 7.62),
        )

    bank = PassiveRailBank(
        top_net="VDD",
        bottom_net="GND",
        members=(member("C1", 50.0), member("C2", 70.0), member("C3", 90.0), member("C4", 110.0)),
        top_extras=(),
    )
    obstacles = {_coordinate(95.0, 50.0), _coordinate(105.0, 50.0)}

    assert (
        _passive_rail_bank_lines(
            bank,
            "/test:bank",
            page_width=200.0,
            compact_local_labels=False,
            obstacles=obstacles,
        )
        is None
    )

    clear_subbank = next(
        subbank
        for subbank in _passive_rail_bank_member_subbanks(bank)
        if [member.ref for member in subbank.members] == ["C2", "C3", "C4"]
    )
    assert (
        _passive_rail_bank_lines(
            clear_subbank,
            "/test:bank:subset",
            page_width=200.0,
            compact_local_labels=False,
            obstacles=obstacles,
        )
        is not None
    )


def test_passive_rail_side_bank_rejects_top_bottom_shorts() -> None:
    def member(ref: str, top_y: float) -> PassiveRailBankMember:
        return PassiveRailBankMember(
            ref=ref,
            top_endpoint=f"{ref}.1",
            bottom_endpoint=f"{ref}.2",
            top_point=PinPoint(x=100.0, y=top_y, label_x=100.0, label_y=top_y),
            bottom_point=PinPoint(
                x=100.0,
                y=top_y + 7.62,
                label_x=100.0,
                label_y=top_y + 7.62,
            ),
        )

    bank = PassiveRailBank(
        top_net="VDD",
        bottom_net="GND",
        members=(member("C1", 50.0), member("C2", 70.0), member("C3", 90.0)),
        top_extras=(
            (
                "U1.OUT",
                PinPoint(x=40.0, y=57.62, label_x=40.0, label_y=57.62),
            ),
        ),
    )
    items = _passive_rail_bank_lines(
        bank,
        "/test:bank",
        page_width=200.0,
        compact_local_labels=False,
        obstacles=set(),
    )
    assert items is not None
    top_label = next(
        item for item in items if isinstance(item, PlacedLabel) and item.name == "VDD"
    )
    bottom_label = next(
        item for item in items if isinstance(item, PlacedLabel) and item.name == "GND"
    )

    assert not _wire_graph_connects(items, top_label.at, bottom_label.at)


def test_passive_rail_bank_routes_clear_passive_fields(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  C1: {lib: Test:C, value: 10uF 100V X7R}
  C2: {lib: Test:C, value: 47uF 10V}
  C3: {lib: Test:C, value: 100nF}
nets:
  VIN:
    - C1.1
    - C2.1
    - C3.1
  GND:
    - C1.2
    - C2.2
    - C3.2
""",
    )
    project = load_project_ir(tmp_path / "project.ksch.yaml")
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    placed = build_placed_project(resolved)
    problem = placed_layout_problem(placed.sheets[0])

    blocked_by_passive_fields = [
        (segment.id, element.id)
        for segment in problem.segments
        for element in problem.blocking_elements(segment)
        if element.kind == "field" and element.owner.startswith("C")
    ]

    assert schematic.exists()
    assert blocked_by_passive_fields == []


def test_point_label_moves_to_clear_field_blockers() -> None:
    point = PinPoint(x=100.0, y=50.0, label_x=105.08, label_y=50.0)
    blocker = Rect(left=103.0, top=48.0, right=112.7, bottom=49.0)

    adjusted = _point_with_clear_label(
        "NET",
        point,
        page_width=200.0,
        blocked_rects=(blocker,),
    )
    adjusted_rect = text_rect(
        Point(adjusted.label_x, adjusted.label_y),
        "NET",
        justify="right" if adjusted.label_x < adjusted.x else "left",
    )

    assert adjusted.label_y == point.y
    assert adjusted.label_x > point.label_x
    assert not adjusted_rect.overlaps(blocker)


def test_point_label_moves_when_field_would_touch_text_clearance() -> None:
    point = PinPoint(x=100.0, y=50.0, label_x=105.08, label_y=50.0)
    label_rect = text_rect(Point(point.label_x, point.label_y), "NET", justify="left")
    blocker = Rect(
        left=label_rect.left,
        top=label_rect.bottom,
        right=label_rect.right,
        bottom=label_rect.bottom + 5.08,
    )

    adjusted = _point_with_clear_label(
        "NET",
        point,
        page_width=200.0,
        blocked_rects=(blocker,),
    )

    assert adjusted.label_x > point.label_x


def test_point_label_relocation_allows_its_original_label_anchor() -> None:
    point = PinPoint(x=187.96, y=158.75, label_x=182.88, label_y=158.75)
    blocker = text_rect(Point(182.88, 161.29), "R46", justify="right")

    adjusted = _point_with_clear_label(
        "Net-(U10-CL)",
        point,
        page_width=420.0,
        blocked_rects=(blocker,),
        obstacles={_coordinate(point.label_x, point.label_y)},
        away_from_x=232.41,
    )

    assert adjusted.label_x < point.label_x


def test_point_label_relocation_can_move_vertical_stub_sideways() -> None:
    point = PinPoint(x=243.84, y=165.1, label_x=243.84, label_y=160.02)

    adjusted = _point_with_clear_label(
        "CM5_5V_IN",
        point,
        page_width=420.0,
        blocked_rects=(),
        obstacles={_coordinate(243.84, 161.29)},
    )

    assert adjusted.label_y == point.y
    assert adjusted.label_x != point.x


def test_point_label_relocation_does_not_land_on_adjacent_pin_obstacle() -> None:
    point = PinPoint(x=165.1, y=58.42, label_x=154.94, label_y=58.42)
    horizontal_lane_blocker = Rect(left=120.0, top=57.15, right=156.21, bottom=59.69)

    adjusted = _point_with_clear_label(
        "CAN_INT",
        point,
        page_width=420.0,
        blocked_rects=(horizontal_lane_blocker,),
        obstacles={_coordinate(165.1, 63.5)},
    )

    assert adjusted.label_y != 63.5


def test_point_label_relocation_prefers_electrical_clearance_over_text_clearance() -> None:
    point = PinPoint(x=299.72, y=181.61, label_x=304.8, label_y=181.61)
    occupied = [(303.53, 177.8, 303.53, 182.88)]
    text_blocker = Rect(left=292.1, top=176.53, right=299.72, bottom=179.07)

    adjusted = _point_with_clear_label(
        "VEH_ILLUM_SENSE",
        point,
        page_width=420.0,
        blocked_rects=(text_blocker,),
        occupied_segments=occupied,
    )

    assert adjusted != point
    assert not _segments_touch(
        (adjusted.x, adjusted.y, adjusted.label_x, adjusted.label_y),
        occupied[0],
    )


def test_same_x_top_or_bottom_points_do_not_make_shared_vertical_rail() -> None:
    items = _rail_lines(
        "VBUS",
        [
            ("C1.1", PinPoint(x=100.0, y=20.0, label_x=100.0, label_y=15.0)),
            ("C2.1", PinPoint(x=100.0, y=40.0, label_x=100.0, label_y=35.0)),
            ("C3.1", PinPoint(x=100.0, y=60.0, label_x=100.0, label_y=55.0)),
        ],
        "/test:VBUS",
        page_width=None,
    )

    assert sum(1 for item in items if isinstance(item, PlacedLabel) and item.name == "VBUS") == 3
    assert not any(isinstance(item, PlacedJunction) for item in items)


def test_shared_right_rails_are_split_at_each_pin_join() -> None:
    items = _rail_lines(
        "VBUS",
        [
            ("C1.1", PinPoint(x=100.0, y=20.0, label_x=105.0, label_y=20.0)),
            ("C2.1", PinPoint(x=100.0, y=22.0, label_x=105.0, label_y=22.0)),
            ("C3.1", PinPoint(x=100.0, y=24.0, label_x=105.0, label_y=24.0)),
        ],
        "/test:VBUS",
        page_width=None,
    )
    segments = {
        _canonical_segment((start, end)) for start, end in _line_wire_segments(items)
    }

    assert ((110.08, 20.0), (110.08, 24.0)) not in segments
    assert ((110.08, 20.0), (110.08, 22.0)) in segments
    assert ((110.08, 22.0), (110.08, 24.0)) in segments


def test_shared_rail_labels_justify_away_from_connected_points() -> None:
    left_lines = _rail_lines(
        "LEFT_NET",
        [
            ("U1.1", PinPoint(x=100.0, y=20.0, label_x=94.92, label_y=20.0)),
            ("U1.2", PinPoint(x=100.0, y=22.0, label_x=94.92, label_y=22.0)),
            ("U1.3", PinPoint(x=100.0, y=24.0, label_x=94.92, label_y=24.0)),
        ],
        "/test:LEFT_NET",
        page_width=None,
    )
    right_lines = _rail_lines(
        "RIGHT_NET",
        [
            ("U1.1", PinPoint(x=100.0, y=20.0, label_x=105.08, label_y=20.0)),
            ("U1.2", PinPoint(x=100.0, y=22.0, label_x=105.08, label_y=22.0)),
            ("U1.3", PinPoint(x=100.0, y=24.0, label_x=105.08, label_y=24.0)),
        ],
        "/test:RIGHT_NET",
        page_width=None,
    )

    left_label = next(item for item in left_lines if isinstance(item, PlacedLabel))
    right_label = next(item for item in right_lines if isinstance(item, PlacedLabel))

    assert left_label.justify == "right"
    assert right_label.justify == "left"


def test_local_point_label_justifies_away_from_parent_ic() -> None:
    lines = _net_point_lines(
        "LOCAL_CL",
        [
            ("R46.1", PinPoint(x=100.0, y=50.0, label_x=105.08, label_y=50.0)),
            ("U10.CL", PinPoint(x=150.0, y=50.0, label_x=144.92, label_y=50.0)),
        ],
        "/test:LOCAL_CL",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=True,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=False,
        compact_local_labels=False,
        local_label_prefix=None,
        dense_controller_refs={"U10"},
        hide_duplicate_labels=False,
        page_width=300.0,
        obstacles=set(),
        occupied_segments=[],
    )
    label = next(item for item in lines if isinstance(item, PlacedLabel))

    assert label.at == (105.08, 50.0)
    assert label.justify == "right"


def test_local_parent_label_moves_to_clear_prior_label() -> None:
    lines = _net_point_lines(
        "RT_CLK",
        [
            ("R4.1", PinPoint(x=100.0, y=50.0, label_x=105.08, label_y=50.0)),
            ("U10.CL", PinPoint(x=150.0, y=50.0, label_x=144.92, label_y=50.0)),
        ],
        "/test:RT_CLK",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=True,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=False,
        compact_local_labels=False,
        local_label_prefix=None,
        dense_controller_refs={"U10"},
        hide_duplicate_labels=False,
        page_width=300.0,
        obstacles=set(),
        occupied_segments=[],
        label_blocked_rects=(
            text_rect(Point(105.08, 50.0), "LOCAL_CL", justify="right"),
        ),
    )
    label = next(item for item in lines if isinstance(item, PlacedLabel))

    assert label.at == (114.3, 50.0)
    assert label.justify == "right"


def test_distant_source_label_uses_local_stub_direction_not_parent_ic() -> None:
    lines = _net_point_lines(
        "LOCAL_CL",
        [
            ("F3.2", PinPoint(x=20.0, y=50.0, label_x=25.08, label_y=50.0)),
            ("U10.CL", PinPoint(x=150.0, y=50.0, label_x=144.92, label_y=50.0)),
        ],
        "/test:LOCAL_CL",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=True,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=False,
        allow_medium_signal_rails=False,
        compact_local_labels=False,
        local_label_prefix=None,
        dense_controller_refs={"U10"},
        hide_duplicate_labels=False,
        page_width=300.0,
        obstacles=set(),
        occupied_segments=[],
    )
    label = next(item for item in lines if isinstance(item, PlacedLabel))

    assert label.at == (25.08, 50.0)
    assert label.justify == "left"


def test_same_symbol_multi_pin_nets_use_discrete_labels(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
nets:
  GND:
    - U1.GND/all
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert text.count('(label "GND"') == 2
    assert "(xy 205.74000000000004 106.68) (xy 205.74000000000004 109.22)" not in text


def test_vertical_stub_moves_sideways_when_it_would_cross_another_pin() -> None:
    point = PinPoint(x=50.8, y=60.96, label_x=50.8, label_y=55.88)

    adjusted = _point_avoiding_obstacle_stub(point, {_coordinate(50.8, 58.42)})

    assert adjusted == PinPoint(x=50.8, y=60.96, label_x=55.88, label_y=60.96)


def test_horizontal_stub_moves_to_clear_label_obstacles_without_crossing_pins() -> None:
    point = PinPoint(x=33.02, y=109.22, label_x=7.62, label_y=109.22)
    obstacles = {
        _coordinate(27.94, 109.22),
        _coordinate(33.02, 106.68),
        _coordinate(33.02, 111.76),
    }

    adjusted = _point_avoiding_obstacle_stub(point, obstacles)

    assert adjusted == PinPoint(x=33.02, y=109.22, label_x=38.1, label_y=109.22)


def test_point_stub_avoids_previously_routed_segments() -> None:
    point = PinPoint(x=232.41, y=162.56, label_x=243.84, label_y=162.56)
    occupied = [(238.76, 160.02, 238.76, 173.99)]

    adjusted = _point_avoiding_obstacle_stub(point, set(), occupied_segments=occupied)

    assert adjusted == PinPoint(x=232.41, y=162.56, label_x=226.06, label_y=162.56)
    assert not _segments_touch(
        (adjusted.x, adjusted.y, adjusted.label_x, adjusted.label_y),
        occupied[0],
    )


def test_low_interface_local_circuits_route_multi_pin_local_nodes_with_junctions(
    tmp_path: Path,
) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  VOUT: power_out
  GND: power_in
symbols:
  J1: {lib: Test:USB_C}
  F1: {lib: Test:C}
  Q1: {lib: Test:C}
  U1: {lib: Test:USBHub}
  L1: {lib: Test:C}
  C1: {lib: Test:C, value: 100nF}
nets:
  VIN:
    - J1.VBUS/all
    - F1.1
  VOUT:
    - L1.2
  GND:
    - U1.GND/all
    - C1.2
  Power Input + 5V_SW:
    - U1.USBDP_UP
    - L1.1
    - C1.1
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert '(label "SW"' in text
    assert "Power Input + 5V_SW" not in text
    assert "(junction" in text


def test_medium_symbol_sheets_route_compact_anchor_passive_nets(tmp_path: Path) -> None:
    extra_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(2, 13)
    )
    extra_nets = "\n".join(
        f"  UNUSED_{index}:\n    - C{index}.1\n" for index in range(2, 13)
    )
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
  C1: {{lib: Test:C, value: 100nF}}
{extra_symbols}
nets:
  LOCAL_DECOUPLE:
    - U1.VBUS_DET
    - C1.1
{extra_nets}
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert _visible_label_count(schematic, "LOCAL_DECOUPLE") == 1
    assert text.count('(label "LOCAL_DECOUPLE"') == 1
    assert _hidden_label_count(schematic) <= 1


def test_anchor_side_passives_place_against_their_parent_pin(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 10k}
  R2: {lib: Test:C, value: 10k}
nets:
  SIG_A:
    - U1.USBDP_UP
    - R1.2
  SENSE_A:
    - R1.1
  SIG_B:
    - U1.USBDM_UP
    - R2.1
  GND:
    - R2.2
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))

    positions = _layout_sheet_symbols(resolved, "/")
    usb_hub = resolved.symbol_library["Test:USBHub"]
    passive = resolved.symbol_library["Test:C"]
    u_position = positions[("U1", 1)]
    r1_position = positions[("R1", 1)]
    r2_position = positions[("R2", 1)]
    u_sig_a_pin = _pin_by_number(usb_hub, "1")
    u_sig_b_pin = _pin_by_number(usb_hub, "2")
    r1_pin = _pin_by_number(passive, "2")
    r2_pin = _pin_by_number(passive, "1")
    assert u_sig_a_pin is not None
    assert u_sig_b_pin is not None
    assert r1_pin is not None
    assert r2_pin is not None
    u_sig_a = _symbol_pin_point(
        u_position.x,
        u_position.y,
        u_sig_a_pin,
        symbol_info=usb_hub,
    )
    u_sig_b = _symbol_pin_point(
        u_position.x,
        u_position.y,
        u_sig_b_pin,
        symbol_info=usb_hub,
    )
    r1_anchor_pin = _symbol_pin_point(
        r1_position.x,
        r1_position.y,
        r1_pin,
        symbol_info=passive,
    )
    r2_anchor_pin = _symbol_pin_point(
        r2_position.x,
        r2_position.y,
        r2_pin,
        symbol_info=passive,
    )

    assert r1_position.x < u_position.x
    assert r2_position.x < u_position.x
    assert abs(r1_anchor_pin.y - u_sig_a.y) < 0.01
    assert abs(r2_anchor_pin.y - u_sig_b.y) < 0.01
    assert round(abs(r1_anchor_pin.y - r2_anchor_pin.y), 2) == round(
        abs(u_sig_a.y - u_sig_b.y),
        2,
    )


def test_low_interface_support_passives_place_from_parent_pin_geometry(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
interface:
  VIN: power_in
  GND: power_in
symbols:
  F1: {lib: Test:C, value: input fuse}
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 10k}
nets:
  VIN:
    - F1.1
  GND:
    - F1.2
    - U1.GND/all
  SIG_A:
    - U1.USBDP_UP
    - R1.2
  SENSE_A:
    - R1.1
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))

    positions = _layout_sheet_symbols(resolved, "/")
    usb_hub = resolved.symbol_library["Test:USBHub"]
    passive = resolved.symbol_library["Test:C"]
    u_position = positions[("U1", 1)]
    r1_position = positions[("R1", 1)]
    u_pin = _pin_by_number(usb_hub, "1")
    r1_pin = _pin_by_number(passive, "2")
    assert u_pin is not None
    assert r1_pin is not None
    u_point = _symbol_pin_point(
        u_position.x,
        u_position.y,
        u_pin,
        symbol_info=usb_hub,
    )
    r1_point = _symbol_pin_point(
        r1_position.x,
        r1_position.y,
        r1_pin,
        symbol_info=passive,
    )

    assert abs(r1_point.y - u_point.y) < 0.01
    assert r1_point.x < u_point.x
    assert abs(r1_point.x - u_point.label_x) <= 20.32


def test_anchor_side_passive_labels_and_routes_face_away_from_parent_ic(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 10k}
nets:
  SIG_A:
    - U1.USBDP_UP
    - R1.2
  SENSE_A:
    - R1.1
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    placed = build_placed_project(resolved)
    sheet = placed.sheets[0]
    sense_label = next(
        item
        for item in sheet.items
        if isinstance(item, PlacedLabel) and item.name == "SENSE_A"
    )
    sig_segments = [
        item
        for item in sheet.items
        if isinstance(item, PlacedWire) and item.nets == frozenset({"SIG_A"})
    ]
    r1_symbol = next(
        item for item in sheet.items if isinstance(item, PlacedSymbol) and item.reference == "R1"
    )
    u1_symbol = next(
        item for item in sheet.items if isinstance(item, PlacedSymbol) and item.reference == "U1"
    )
    sig_label = next(
        item for item in sheet.items if isinstance(item, PlacedLabel) and item.name == "SIG_A"
    )
    r1_info = resolved.symbol_library["Test:C"]
    u1_info = resolved.symbol_library["Test:USBHub"]
    r1_pin_symbol = _pin_by_number(r1_info, "2")
    u1_pin_symbol = _pin_by_number(u1_info, "1")
    assert r1_pin_symbol is not None
    assert u1_pin_symbol is not None
    r1_pin = _symbol_pin_point(
        r1_symbol.at[0],
        r1_symbol.at[1],
        r1_pin_symbol,
        symbol_info=r1_info,
    )
    u1_pin = _symbol_pin_point(
        u1_symbol.at[0],
        u1_symbol.at[1],
        u1_pin_symbol,
        symbol_info=u1_info,
    )

    assert sense_label.justify == "right"
    assert sig_label.justify == "right"
    assert sig_label.at[0] < r1_pin.x
    assert sig_label.at[1] == r1_pin.y
    assert _wire_path_covers_horizontal_segment(
        [(segment.start, segment.end) for segment in sig_segments],
        (r1_pin.x, r1_pin.y),
        (u1_pin.x, u1_pin.y),
    )


def test_anchor_passive_topology_routes_pin_to_pin_not_label_only(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 10k}
nets:
  SIG_A:
    - U1.USBDP_UP
    - R1.2
  SENSE_A:
    - R1.1
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    placed = build_placed_project(resolved)
    sheet = placed.sheets[0]
    positions = _layout_sheet_symbols(resolved, "/")
    usb_hub = resolved.symbol_library["Test:USBHub"]
    passive = resolved.symbol_library["Test:C"]
    u_position = positions[("U1", 1)]
    r1_position = positions[("R1", 1)]
    u_pin = _pin_by_number(usb_hub, "1")
    r1_pin = _pin_by_number(passive, "2")
    assert u_pin is not None
    assert r1_pin is not None
    u_point = _symbol_pin_point(
        u_position.x,
        u_position.y,
        u_pin,
        symbol_info=usb_hub,
    )
    r1_point = _symbol_pin_point(
        r1_position.x,
        r1_position.y,
        r1_pin,
        symbol_info=passive,
    )
    sig_wires = [
        item
        for item in sheet.items
        if isinstance(item, PlacedWire) and item.nets == frozenset({"SIG_A"})
    ]
    sig_labels = [
        item
        for item in sheet.items
        if isinstance(item, PlacedLabel) and item.name == "SIG_A" and not item.hidden
    ]

    assert _wire_graph_connects(sig_wires, (u_point.x, u_point.y), (r1_point.x, r1_point.y))
    assert len(sig_labels) == 1


def test_grounded_vertical_two_pin_symbol_rotates_ground_pin_down(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  C1: {lib: Test:C, value: 10nF}
nets:
  LOCAL:
    - U1.VBUS_DET
    - C1.2
  GND:
    - C1.1
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    placed = build_placed_project(resolved)
    sheet = placed.sheets[0]
    c1_symbol = next(
        item for item in sheet.items if isinstance(item, PlacedSymbol) and item.reference == "C1"
    )
    cap_info = resolved.symbol_library["Test:C"]
    ground_pin = _pin_by_number(cap_info, "1")
    local_pin = _pin_by_number(cap_info, "2")
    assert ground_pin is not None
    assert local_pin is not None

    ground_point = _symbol_pin_point(
        c1_symbol.at[0],
        c1_symbol.at[1],
        ground_pin,
        symbol_info=cap_info,
        symbol_rotation=c1_symbol.rotation,
    )
    local_point = _symbol_pin_point(
        c1_symbol.at[0],
        c1_symbol.at[1],
        local_pin,
        symbol_info=cap_info,
        symbol_rotation=c1_symbol.rotation,
    )
    gnd_wires = [
        item
        for item in sheet.items
        if isinstance(item, PlacedWire) and item.nets == frozenset({"GND"})
    ]
    gnd_labels = [
        item
        for item in sheet.items
        if isinstance(item, PlacedLabel) and item.name == "GND" and not item.hidden
    ]

    assert c1_symbol.rotation == 180
    assert ground_point.y > local_point.y
    assert any(
        segment.start == (ground_point.x, ground_point.y)
        and segment.end[0] == ground_point.x
        and segment.end[1] > ground_point.y
        for segment in gnd_wires
    )
    assert any(
        label.at[0] == ground_point.x and label.at[1] > ground_point.y
        for label in gnd_labels
    )


def test_grounded_horizontal_two_pin_symbol_rotates_ground_pin_down(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  D1: {lib: Test:H2}
nets:
  OUT:
    - D1.K
  GND:
    - D1.A
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    placed = build_placed_project(resolved)
    sheet = placed.sheets[0]
    d1_symbol = next(
        item for item in sheet.items if isinstance(item, PlacedSymbol) and item.reference == "D1"
    )
    switch_info = resolved.symbol_library["Test:H2"]
    ground_pin = _pin_by_number(switch_info, "2")
    output_pin = _pin_by_number(switch_info, "1")
    assert ground_pin is not None
    assert output_pin is not None

    ground_point = _symbol_pin_point(
        d1_symbol.at[0],
        d1_symbol.at[1],
        ground_pin,
        symbol_info=switch_info,
        symbol_rotation=d1_symbol.rotation,
    )
    output_point = _symbol_pin_point(
        d1_symbol.at[0],
        d1_symbol.at[1],
        output_pin,
        symbol_info=switch_info,
        symbol_rotation=d1_symbol.rotation,
    )
    gnd_labels = [
        item
        for item in sheet.items
        if isinstance(item, PlacedLabel) and item.name == "GND" and not item.hidden
    ]

    assert d1_symbol.rotation == 270
    assert ground_point.y > output_point.y
    assert any(
        label.at[0] == ground_point.x and label.at[1] > ground_point.y
        for label in gnd_labels
    )


def test_multi_pin_symbol_bottom_ground_pin_escapes_below_body() -> None:
    symbol_info = SymbolInfo(
        lib_id="Test:BottomGroundIc",
        name="BottomGroundIc",
        footprint=None,
        pins=[
            SymbolPin("IN", "1", "input", at=(-7.62, 0.0, 0.0)),
            SymbolPin("OUT", "2", "output", at=(7.62, 0.0, 180.0)),
            SymbolPin("GND", "3", "power_in", at=(0.0, -12.7, 270.0)),
        ],
        definition=[
            "symbol",
            "BottomGroundIc",
            [
                "symbol",
                "BottomGroundIc_1_1",
                [
                    "rectangle",
                    ["start", -5.08, 5.08],
                    ["end", 5.08, -10.16],
                ],
            ],
        ],
    )

    ground_pin = next(pin for pin in symbol_info.pins if pin.name == "GND")
    point = _symbol_pin_point(
        100.0,
        100.0,
        ground_pin,
        symbol_info=symbol_info,
    )

    assert point.label_x == point.x
    assert point.y > 100.0
    assert point.label_y > point.y


def test_library_no_connect_pins_do_not_emit_redundant_markers(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:NCDevice}
no_connects:
  - U1.NC
  - U1.IN
""",
    )

    assert schematic.read_text(encoding="utf-8").count("(no_connect") == 1


def test_anchor_passive_direct_route_avoids_other_pin_label_obstacles() -> None:
    obstacle = _coordinate(200.66, 140.97)

    lines = _net_point_lines(
        "RT_CLK",
        [
            (
                "U1.RT/CLK",
                PinPoint(x=231.14, y=114.3, label_x=226.06, label_y=114.3),
            ),
            ("R4.1", PinPoint(x=187.96, y=146.05, label_x=187.96, label_y=140.97)),
        ],
        "/power_input_5v:Power Input + 5V_RT_CLK",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=True,
        allow_medium_signal_rails=False,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs={"U1"},
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={obstacle},
        occupied_segments=[],
    )

    assert all(
        not _point_on_segment(obstacle, (*start, *end))
        for start, end in _line_wire_segments(lines)
    )


def test_anchor_passive_direct_route_uses_trunk_label_when_pin_lane_is_blocked() -> None:
    obstacle = _coordinate(187.96, 166.37)

    lines = _net_point_lines(
        "Net-(U10-IN)",
        [
            ("U10.IN", PinPoint(x=232.41, y=160.02, label_x=227.33, label_y=160.02)),
            ("R47.2", PinPoint(x=200.66, y=166.37, label_x=195.58, label_y=166.37)),
        ],
        "/power_input_5v:Net-(U10-IN)",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=True,
        allow_medium_signal_rails=False,
        compact_local_labels=False,
        local_label_prefix=None,
        dense_controller_refs={"U10"},
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles={obstacle},
        occupied_segments=[],
        label_blocked_rects=(
            text_rect(Point(182.88, 163.83), "499R 1%", justify="right"),
            text_rect(Point(195.58, 163.83), "10k", justify="right"),
        ),
    )
    label = next(item for item in lines if isinstance(item, PlacedLabel))

    assert label.at[1] > 166.37
    assert all(
        not _point_on_segment(obstacle, (*start, *end))
        for start, end in _line_wire_segments(lines)
    )


def test_topology_anchor_passive_route_does_not_drop_wire_for_blocked_label() -> None:
    anchor = PinPoint(x=232.41, y=160.02, label_x=227.33, label_y=160.02)
    passive = PinPoint(x=200.66, y=166.37, label_x=195.58, label_y=166.37)

    blocked_route = _safe_anchor_passive_direct_net_lines(
        "Net-(U10-IN)",
        [("U10.IN", anchor), ("R47.2", passive)],
        "/power_input_5v:Net-(U10-IN)",
        page_width=420.0,
        obstacles=set(),
        occupied_segments=[],
        label_blocked_rects=(Rect(left=0.0, top=0.0, right=420.0, bottom=297.0),),
    )
    topology_route = _safe_anchor_passive_direct_net_lines(
        "Net-(U10-IN)",
        [("U10.IN", anchor), ("R47.2", passive)],
        "/power_input_5v:Net-(U10-IN):topology",
        page_width=420.0,
        obstacles=set(),
        occupied_segments=[],
        label_blocked_rects=(Rect(left=0.0, top=0.0, right=420.0, bottom=297.0),),
        require_clear_label=False,
    )

    assert blocked_route is None
    assert topology_route is not None
    wires = [item for item in topology_route if isinstance(item, PlacedWire)]
    assert _wire_graph_connects(wires, (anchor.x, anchor.y), (passive.x, passive.y))


def test_topology_anchor_passive_net_lines_require_clear_labels_from_call_site() -> None:
    anchor = PinPoint(x=232.41, y=160.02, label_x=227.33, label_y=160.02)
    passive = PinPoint(x=200.66, y=166.37, label_x=195.58, label_y=166.37)

    lines = _net_point_lines(
        "Net-(U10-IN)",
        [("U10.IN", anchor), ("R47.2", passive)],
        "/power_input_5v:Net-(U10-IN)",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=True,
        allow_medium_signal_rails=False,
        compact_local_labels=False,
        local_label_prefix=None,
        dense_controller_refs={"U10"},
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles=set(),
        occupied_segments=[],
        label_blocked_rects=(Rect(left=0.0, top=0.0, right=420.0, bottom=297.0),),
    )

    assert not any(isinstance(item, PlacedLabel) and not item.hidden for item in lines)


def test_topology_anchor_passive_route_handles_vertical_anchor_pins() -> None:
    anchor = PinPoint(x=232.41, y=165.1, label_x=232.41, label_y=170.18)
    passive = PinPoint(x=187.96, y=158.75, label_x=187.96, label_y=152.4)
    other_passive_pin = _coordinate(187.96, 166.37)

    route = _safe_anchor_passive_direct_net_lines(
        "Net-(U10-CL)",
        [("U10.CL", anchor), ("R46.1", passive)],
        "/power_input_5v:Net-(U10-CL):topology",
        page_width=420.0,
        obstacles={other_passive_pin},
        occupied_segments=[],
        require_clear_label=False,
    )

    assert route is not None
    wires = [item for item in route if isinstance(item, PlacedWire)]
    assert _wire_graph_connects(wires, (anchor.x, anchor.y), (passive.x, passive.y))
    assert all(
        not _point_on_segment(other_passive_pin, (*wire.start, *wire.end))
        for wire in wires
    )


def test_topology_anchor_passive_escape_route_avoids_neighbor_pin_lane() -> None:
    anchor = PinPoint(x=232.41, y=165.1, label_x=227.33, label_y=165.1)
    passive = PinPoint(x=187.96, y=158.75, label_x=182.88, label_y=158.75)
    occupied_segments = [
        (195.58, 166.37, 200.66, 166.37),
        (223.52, 160.02, 232.41, 160.02),
        (223.52, 160.02, 223.52, 172.72),
        (200.66, 172.72, 223.52, 172.72),
        (200.66, 166.37, 200.66, 172.72),
    ]

    route = _safe_anchor_passive_direct_net_lines(
        "Net-(U10-CL)",
        [("U10.CL", anchor), ("R46.1", passive)],
        "/power_input_5v:Net-(U10-CL):topology",
        page_width=420.0,
        obstacles={_coordinate(187.96, 166.37), _coordinate(200.66, 166.37)},
        occupied_segments=occupied_segments,
        require_clear_label=False,
    )

    assert route is not None
    wires = [item for item in route if isinstance(item, PlacedWire)]
    assert _wire_graph_connects(wires, (anchor.x, anchor.y), (passive.x, passive.y))
    assert all(
        not _segments_touch((*wire.start, *wire.end), existing)
        for wire in wires
        for existing in occupied_segments[:5]
    )


def test_net_routing_items_carry_source_net_metadata() -> None:
    items = _net_point_lines(
        "RT_CLK",
        [
            ("U1.RT/CLK", PinPoint(x=231.14, y=114.3, label_x=226.06, label_y=114.3)),
            ("R4.1", PinPoint(x=187.96, y=146.05, label_x=187.96, label_y=140.97)),
        ],
        "/power_input_5v:Power Input + 5V_RT_CLK",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=True,
        allow_medium_signal_rails=False,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs={"U1"},
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles=set(),
        occupied_segments=[],
    )

    assert {
        item.nets
        for item in items
        if isinstance(item, PlacedWire | PlacedLabel | PlacedJunction)
    } == {frozenset({"Power Input + 5V_RT_CLK"})}
    assert {
        terminal
        for item in items
        if isinstance(item, PlacedWire)
        for terminal in item.start_terminals | item.end_terminals
    } == {"U1.RT/CLK", "R4.1"}


def test_anchor_passive_direct_routes_avoid_previously_emitted_wires() -> None:
    occupied_segments: list[tuple[float, float, float, float]] = []
    _net_point_lines(
        "BUCK_EN",
        [
            ("U1.EN", PinPoint(x=231.14, y=109.22, label_x=226.06, label_y=109.22)),
            ("R3.2", PinPoint(x=187.96, y=133.35, label_x=187.96, label_y=138.43)),
        ],
        "/power_input_5v:Power Input + 5V_BUCK_EN",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=True,
        allow_medium_signal_rails=False,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs={"U1"},
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles=set(),
        occupied_segments=occupied_segments,
    )
    first_route_segments = list(occupied_segments)

    _net_point_lines(
        "COMP",
        [
            ("U1.COMP", PinPoint(x=231.14, y=111.76, label_x=226.06, label_y=111.76)),
            ("R2.2", PinPoint(x=187.96, y=113.03, label_x=187.96, label_y=118.11)),
        ],
        "/power_input_5v:Power Input + 5V_COMP",
        allow_shared_rails=False,
        allow_direct_nets=False,
        allow_safe_direct_nets=False,
        allow_safe_local_rails=False,
        allow_anchor_direct_nets=False,
        allow_anchor_passive_direct_nets=True,
        allow_medium_signal_rails=False,
        compact_local_labels=True,
        local_label_prefix="Power Input + 5V_",
        dense_controller_refs={"U1"},
        hide_duplicate_labels=True,
        page_width=420.0,
        obstacles=set(),
        occupied_segments=occupied_segments,
    )

    second_route_segments = occupied_segments[len(first_route_segments) :]
    assert all(
        not _segments_touch(first, second)
        for first in first_route_segments
        for second in second_route_segments
    )


def test_powerish_detection_ignores_imported_title_prefix_for_local_nodes() -> None:
    assert not _is_powerish_net("Power Input + 5V_FB")
    assert not _is_powerish_net("Power Input + 5V_SW")
    assert _is_powerish_net("CM5_5V_IN")
    assert _is_powerish_net("VBAT_PROT")


def test_symbol_sheets_show_discrete_fallback_labels(tmp_path: Path) -> None:
    extra_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(2, 13)
    )
    extra_nets = "\n".join(
        f"  UNUSED_{index}:\n    - C{index}.1\n" for index in range(2, 13)
    )
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
  C1: {{lib: Test:C, value: 100nF}}
  C13: {{lib: Test:C, value: 10k}}
{extra_symbols}
nets:
  VBAT_PROT:
    - U1.VBUS_DET
    - C1.1
    - C13.1
{extra_nets}
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert text.count('(label "VBAT_PROT"') == 3
    assert _visible_label_count(schematic, "VBAT_PROT") == 3
    assert _hidden_label_count(schematic) <= 1


def test_symbol_instance_footprints_are_hidden_on_schematic(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  J1:
    lib: Test:USB_C
    footprint: TestFootprints:USB_Test
nets:
  VBUS:
    - J1.VBUS/all
""",
    )

    symbol = _placed_symbol(schematic, "J1")
    footprint = _property_expr(symbol, "Footprint")

    assert atom(footprint[2]) == "TestFootprints:USB_Test"
    assert _contains_atom(footprint, "hide")
    assert _contains_atom(footprint, "yes")


def test_local_two_pin_nets_route_directly_with_single_label(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  C1: {lib: Test:C, value: 100nF}
nets:
  VBUS_DECOUPLE:
    - U1.VBUS_DET
    - C1.1
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert _visible_label_count(schematic, "VBUS_DECOUPLE") == 1
    assert text.count('(label "VBUS_DECOUPLE"') == 1
    assert _hidden_label_count(schematic) == 0
    assert "(justify right)" in text
    assert text.count("(wire") >= 2


def test_root_child_sheets_use_grid_and_two_sided_pins(tmp_path: Path) -> None:
    root = tmp_path / "project.ksch.yaml"
    sheet_names = ["alpha", "beta", "gamma"]
    root.write_text(
        "ksch: 1\n"
        "project:\n"
        "  name: layout_demo\n"
        "sheets:\n"
        + "\n".join(f"  {name}: {{source: {name}.ksch.yaml}}" for name in sheet_names)
        + "\nnets:\n"
        + "\n".join(f"  NET_{name}: [\"{name}.P01\"]" for name in sheet_names)
        + "\n",
        encoding="utf-8",
    )
    for name in sheet_names:
        interface = "\n".join(f"  P{index:02d}: passive" for index in range(1, 21))
        (tmp_path / f"{name}.ksch.yaml").write_text(
            f"ksch: 1\nsheet:\n  id: {name}\ninterface:\n{interface}\n",
            encoding="utf-8",
        )
    migrate_file_to_connects(root)
    project = load_project_ir(root)
    resolved = resolve_project(project, LibraryContext(symbols={}, footprints={}))
    out = tmp_path / "out"

    write_project(resolved, out)

    text = (out / "layout_demo.kicad_sch").read_text(encoding="utf-8")
    assert '(paper "A3")' in text
    assert text.count("(at 25.4 ") > 0
    assert text.count("(at 165.1 ") > 0
    assert text.count("(at 304.8 ") > 0
    assert " 0)\n      (uuid" in text


def test_root_sheet_port_labels_use_wire_stubs(tmp_path: Path) -> None:
    root = tmp_path / "project.ksch.yaml"
    root.write_text(
        """\
ksch: 1
project:
  name: layout_demo
sheets:
  lefty: {source: lefty.ksch.yaml}
  righty: {source: righty.ksch.yaml}
nets:
  TO_LEFT: ["lefty.IN"]
  FROM_RIGHT: ["righty.OUT"]
""",
        encoding="utf-8",
    )
    (tmp_path / "lefty.ksch.yaml").write_text(
        """\
ksch: 1
sheet:
  id: lefty
interface:
  IN: input
""",
        encoding="utf-8",
    )
    (tmp_path / "righty.ksch.yaml").write_text(
        """\
ksch: 1
sheet:
  id: righty
interface:
  OUT: output
""",
        encoding="utf-8",
    )
    migrate_file_to_connects(root)
    project = load_project_ir(root)
    resolved = resolve_project(project, LibraryContext(symbols={}, footprints={}))
    out = tmp_path / "out"

    write_project(resolved, out)

    segments = [
        ((round(start[0], 2), round(start[1], 2)), (round(end[0], 2), round(end[1], 2)))
        for start, end in _wire_segments(out / "layout_demo.kicad_sch")
    ]
    assert ((25.4, 58.42), (15.24, 58.42)) in segments
    assert ((165.1, 58.42), (154.94, 58.42)) in segments


def test_complex_child_sheet_hierarchy_labels_attach_to_local_net_points(
    tmp_path: Path,
) -> None:
    interface = "\n".join(f"  P{index:02d}: passive" for index in range(1, 10))
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
interface:
{interface}
symbols:
  U1: {{lib: Test:USBHub}}
nets:
  P01:
    - U1.USBDP_UP
""",
    )

    hierarchy_label = _named_expr(schematic, "hierarchical_label", "P01")
    hierarchy_point = _expr_at(hierarchy_label)

    assert hierarchy_point != (25.4, 25.4)
    assert any(end == hierarchy_point for _start, end in _wire_segments(schematic))

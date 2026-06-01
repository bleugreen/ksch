from ksch.layout import Point, Rect
from ksch.layout_solver import _wire_items_avoiding
from ksch.power_flags import POWER_PORT_LIB_ID, power_port_symbol, power_port_symbol_definition
from ksch.placed import PlacedProperty, PlacedSymbol
from ksch.schematic_geometry import (
    LayoutElement,
    LayoutProblem,
    LayoutSegment,
    SchematicGeometry,
    placed_items_geometry,
    segment_blocked_by_element,
    text_rect,
)


def test_layout_problem_reports_overlapping_symbol_bodies() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1", owner="U1", kind="symbol", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="C1", owner="C1", kind="symbol", rect=Rect(10, 10, 14, 14)),
        )
    )

    overlaps = problem.overlaps()

    assert [(hit.first.id, hit.second.id) for hit in overlaps] == [("U1", "C1")]


def test_layout_problem_reports_same_owner_visible_overlaps() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1:body", owner="U1", kind="symbol", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="U1:value", owner="U1", kind="field", rect=Rect(5, 5, 10, 8)),
        )
    )

    assert [(hit.first.id, hit.second.id) for hit in problem.overlaps()] == [
        ("U1:body", "U1:value")
    ]


def test_layout_problem_ignores_same_owner_symbol_body_self_overlap() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1:body:0", owner="U1", kind="symbol_body", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="U1:body:1", owner="U1", kind="symbol_body", rect=Rect(10, 10, 30, 30)),
        )
    )

    assert problem.overlaps() == ()


def test_rects_that_only_touch_with_float_noise_do_not_overlap() -> None:
    first = Rect(left=0.0, top=0.0, right=2.54, bottom=63.5)
    second = Rect(left=0.0, top=63.5 - 1e-12, right=2.54, bottom=66.04)

    assert not first.overlaps(second)


def test_text_rect_tracks_left_and_right_justified_anchors() -> None:
    left = text_rect(Point(10, 20), "CM5_5V_IN", justify="left")
    right = text_rect(Point(10, 20), "CM5_5V_IN", justify="right")

    assert left.left == 10
    assert left.right > 10
    assert right.left < 10
    assert right.right == 10
    assert left.top < 20 < left.bottom


def test_symbol_property_geometry_uses_effective_kicad_rotation() -> None:
    symbol = PlacedSymbol(
        lib_id="Device:R",
        at=(0.0, 0.0),
        unit=1,
        uuid="R1",
        project_name="demo",
        sheet_instance_path="/",
        reference="R1",
        rotation=90,
        properties=(PlacedProperty("Value", "100k", (10.0, 20.0), rotation=0),),
    )

    geometry = placed_items_geometry((symbol,), symbol_library={})
    value_box = next(box for box in geometry.boxes if box.kind == "field")

    assert value_box.rect == text_rect(Point(10.0, 20.0), "100k", rotation=90)


def test_counter_rotated_symbol_property_geometry_is_upright() -> None:
    symbol = PlacedSymbol(
        lib_id="Device:R",
        at=(0.0, 0.0),
        unit=1,
        uuid="R1",
        project_name="demo",
        sheet_instance_path="/",
        reference="R1",
        rotation=90,
        properties=(PlacedProperty("Value", "100k", (10.0, 20.0), rotation=270),),
    )

    geometry = placed_items_geometry((symbol,), symbol_library={})
    value_box = next(box for box in geometry.boxes if box.kind == "field")

    assert value_box.rect == text_rect(Point(10.0, 20.0), "100k", rotation=0)


def test_half_turn_symbol_property_geometry_uses_absolute_field_rotation() -> None:
    symbol = PlacedSymbol(
        lib_id="Device:R",
        at=(0.0, 0.0),
        unit=1,
        uuid="R1",
        project_name="demo",
        sheet_instance_path="/",
        reference="R1",
        rotation=180,
        properties=(PlacedProperty("Value", "100k", (10.0, 20.0), rotation=0),),
    )

    geometry = placed_items_geometry((symbol,), symbol_library={})
    value_box = next(box for box in geometry.boxes if box.kind == "field")

    assert value_box.rect == text_rect(Point(10.0, 20.0), "100k", rotation=0)


def test_generated_power_port_geometry_counts_only_visible_value_field() -> None:
    symbol = power_port_symbol(
        "/",
        "CM5_3V3_OUT",
        "U1:VDD",
        Point(10.0, 20.0),
        Point(12.54, 20.0),
    )

    geometry = placed_items_geometry(
        (symbol,),
        symbol_definitions={POWER_PORT_LIB_ID: power_port_symbol_definition()},
    )

    assert [(box.kind, box.owner) for box in geometry.boxes] == [
        ("field", symbol.reference)
    ]
    assert geometry.boxes[0].rect == text_rect(Point(12.54, 20.0), "CM5_3V3_OUT")


def test_layout_problem_reports_cross_net_segment_contacts() -> None:
    problem = LayoutProblem(
        segments=(
            LayoutSegment(
                id="vdd",
                owner="C1",
                kind="wire",
                start=Point(0, 10),
                end=Point(20, 10),
                nets=frozenset({"VDD"}),
            ),
            LayoutSegment(
                id="gnd",
                owner="C2",
                kind="wire",
                start=Point(10, 0),
                end=Point(10, 20),
                nets=frozenset({"GND"}),
            ),
            LayoutSegment(
                id="vdd-2",
                owner="C3",
                kind="wire",
                start=Point(15, 5),
                end=Point(15, 15),
                nets=frozenset({"VDD"}),
            ),
        )
    )

    contacts = problem.cross_net_contacts()

    assert [(hit.first.id, hit.second.id, hit.point) for hit in contacts] == [
        ("vdd", "gnd", Point(10, 10))
    ]


def test_layout_problem_reports_route_segment_blocked_by_text() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(
                id="C1:value",
                owner="C1",
                kind="field",
                rect=text_rect(Point(10, 10), "100nF", justify="left"),
            ),
        )
    )
    segment = LayoutSegment(
        id="route",
        owner="net",
        kind="wire",
        start=Point(0, 10),
        end=Point(30, 10),
        nets=frozenset({"VDD"}),
    )

    assert [hit.id for hit in problem.blocking_elements(segment)] == ["C1:value"]


def test_route_blockers_ignore_zero_length_label_anchors() -> None:
    geometry = SchematicGeometry(
        boxes=(
            LayoutElement(
                id="U1:body",
                owner="U1",
                kind="symbol_body",
                rect=Rect(0, 0, 20, 20),
            ),
        ),
        segments=(
            LayoutSegment(
                id="label:anchor",
                owner="NET",
                kind="hierarchical_label_anchor",
                start=Point(10, 10),
                end=Point(10, 10),
                nets=frozenset({"NET"}),
            ),
            LayoutSegment(
                id="wire",
                owner="NET",
                kind="wire",
                start=Point(-5, 10),
                end=Point(25, 10),
                nets=frozenset({"NET"}),
            ),
        ),
    )

    assert [(segment.id, box.id) for segment, box in geometry.route_blockers()] == [
        ("wire", "U1:body")
    ]


def test_route_blockers_report_terminal_wire_through_own_symbol_body() -> None:
    geometry = SchematicGeometry(
        boxes=(
            LayoutElement(
                id="R1:body",
                owner="R1",
                kind="symbol_body",
                rect=Rect(10, 8, 20, 12),
            ),
        ),
        segments=(
            LayoutSegment(
                id="wire-through-body",
                owner="NET",
                kind="wire",
                start=Point(0, 10),
                end=Point(30, 10),
                nets=frozenset({"NET"}),
                end_terminals=frozenset({"R1.1@1"}),
            ),
        ),
    )

    assert [(segment.id, box.id) for segment, box in geometry.route_blockers()] == [
        ("wire-through-body", "R1:body")
    ]


def test_route_blockers_allow_terminal_wire_touching_body_edge() -> None:
    geometry = SchematicGeometry(
        boxes=(
            LayoutElement(
                id="R1:body",
                owner="R1",
                kind="symbol_body",
                rect=Rect(10, 8, 20, 12),
            ),
        ),
        segments=(
            LayoutSegment(
                id="pin-edge-stub",
                owner="NET",
                kind="wire",
                start=Point(10, 10),
                end=Point(0, 10),
                nets=frozenset({"NET"}),
                start_terminals=frozenset({"R1.1@1"}),
            ),
        ),
    )

    assert geometry.route_blockers() == ()


def test_route_blockers_allow_terminal_wire_into_one_pin_symbol_body() -> None:
    geometry = SchematicGeometry(
        boxes=(
            LayoutElement(
                id="TP1:body",
                owner="TP1",
                kind="symbol_body",
                rect=Rect(10, 8, 20, 12),
                terminals=frozenset({"TP1.1@1"}),
            ),
        ),
        segments=(
            LayoutSegment(
                id="wire-to-testpoint",
                owner="NET",
                kind="wire",
                start=Point(0, 10),
                end=Point(15, 10),
                nets=frozenset({"NET"}),
                end_terminals=frozenset({"TP1.1@1"}),
            ),
        ),
    )

    assert geometry.route_blockers() == ()


def test_avoiding_router_uses_canonical_blockers() -> None:
    blocker = LayoutElement(
        id="C1:body",
        owner="C1",
        kind="symbol_body",
        rect=Rect(8, -2, 12, 2),
    )

    wires = _wire_items_avoiding(
        "/",
        "NET",
        (0.0, 0.0),
        (20.0, 0.0),
        "U1.1@1",
        "C1.1@1",
        "test",
        [blocker],
        [],
    )

    assert wires
    assert any(abs(wire.start[1]) > 0.001 or abs(wire.end[1]) > 0.001 for wire in wires)
    assert not any(
        segment_blocked_by_element(
            LayoutSegment(
                id=wire.uuid,
                owner="NET",
                kind="wire",
                start=Point(wire.start[0], wire.start[1]),
                end=Point(wire.end[0], wire.end[1]),
                nets=frozenset({"NET"}),
                start_terminals=wire.start_terminals,
                end_terminals=wire.end_terminals,
            ),
            blocker,
        )
        for wire in wires
    )

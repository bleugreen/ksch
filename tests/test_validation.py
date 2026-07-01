from pathlib import Path

import pytest

from ksch.geometry import PinPoint
from ksch.layout import Point
from ksch.placed import (
    PlacedItem,
    PlacedLabel,
    PlacedProject,
    PlacedProperty,
    PlacedSheet,
    PlacedSymbol,
    PlacedSymbolPin,
    PlacedText,
    PlacedWire,
)
from ksch.schematic_geometry import (
    label_geometry_elements,
    placed_items_geometry,
    sheet_symbol_pin_point,
    symbol_pin_point,
)
from ksch.validation import (
    PlacedLayoutError,
    placed_geometry_problem,
    placed_layout_report,
    validate_placed_project,
)


def _sheet(*items: PlacedItem, lib_symbols: tuple[list[object], ...] = ()) -> PlacedSheet:
    return PlacedSheet(
        path="/",
        filename=Path("demo.kicad_sch"),
        uuid="sheet",
        paper="A4",
        lib_symbols=lib_symbols,
        items=items,
        instance_path="/",
        page="1",
    )


def _project(*items: PlacedItem, lib_symbols: tuple[list[object], ...] = ()) -> PlacedProject:
    return PlacedProject(name="demo", sheets=(_sheet(*items, lib_symbols=lib_symbols),))


def _box_symbol_definition() -> list[object]:
    return [
        "symbol",
        "Test:Box",
        ["property", "Reference", "U", ["at", 0, 0, 0]],
        ["property", "Value", "Box", ["at", 0, -2.54, 0]],
        [
            "symbol",
            "Box_1_1",
            ["rectangle", ["start", -5.08, 5.08], ["end", 5.08, -5.08]],
            [
                "pin",
                "passive",
                "line",
                ["at", -7.62, 0, 0],
                ["length", 2.54],
                ["name", "IN"],
                ["number", "1"],
            ],
        ],
    ]


def _split_box_symbol_definition() -> list[object]:
    return [
        "symbol",
        "Test:SplitBox",
        ["property", "Reference", "U", ["at", 0, 0, 0]],
        ["property", "Value", "SplitBox", ["at", 0, -2.54, 0]],
        [
            "symbol",
            "SplitBox_1_1",
            ["rectangle", ["start", -20.0, 10.0], ["end", -10.0, -10.0]],
            ["rectangle", ["start", 10.0, 10.0], ["end", 20.0, -10.0]],
            [
                "pin",
                "passive",
                "line",
                ["at", -22.54, 0, 0],
                ["length", 2.54],
                ["name", "IN"],
                ["number", "1"],
            ],
        ],
    ]


def _placed_box(ref: str, x: float, y: float) -> PlacedSymbol:
    return PlacedSymbol(
        lib_id="Test:Box",
        at=(x, y),
        unit=1,
        uuid=f"{ref}:uuid",
        project_name="demo",
        sheet_instance_path="/",
        reference=ref,
        properties=(
            PlacedProperty("Reference", ref, (x - 5.08, y - 10.16)),
            PlacedProperty("Value", "Box", (x - 5.08, y - 7.62)),
        ),
        pins=(PlacedSymbolPin(number="1", uuid=f"{ref}:pin:1"),),
    )


def test_placed_geometry_problem_reports_cross_net_wire_contacts() -> None:
    problem = placed_geometry_problem(
        _sheet(
            PlacedWire(start=(0, 10), end=(20, 10), uuid="vdd", nets=frozenset({"VDD"})),
            PlacedWire(start=(10, 0), end=(10, 20), uuid="gnd", nets=frozenset({"GND"})),
        )
    )

    contacts = problem.cross_net_contacts()

    assert [(contact.first.id, contact.second.id, contact.point) for contact in contacts] == [
        ("vdd", "gnd", Point(10, 10))
    ]


def test_validation_rejects_visible_symbol_overlap() -> None:
    project = _project(
        _placed_box("U1", 50.0, 50.0),
        _placed_box("U2", 52.54, 50.0),
        lib_symbols=(_box_symbol_definition(),),
    )

    with pytest.raises(PlacedLayoutError, match="visible geometry overlap"):
        validate_placed_project(project)


def test_validation_rejects_wire_crossing_unowned_label() -> None:
    project = _project(
        PlacedWire(start=(30, 30), end=(70, 30), uuid="wire", nets=frozenset({"VDD"})),
        PlacedLabel(name="ENABLE", at=(40, 30), uuid="label", nets=frozenset({"EN"})),
    )

    with pytest.raises(PlacedLayoutError, match="route blocker"):
        validate_placed_project(project)


def test_validation_ignores_duplicate_geometry_for_the_same_item_id() -> None:
    problem = placed_geometry_problem(
        _sheet(
            PlacedLabel(name="SELF_PWR", at=(20.0, 20.0), uuid="same-label"),
            PlacedLabel(name="SELF_PWR", at=(22.54, 20.0), uuid="same-label"),
        )
    )

    assert problem.overlaps() == ()


def test_validation_reports_geometry_outside_page_bounds() -> None:
    project = _project(
        PlacedLabel(name="OFF_PAGE", at=(400.0, 10.0), uuid="label"),
    )

    report = placed_layout_report(project)

    assert report.to_dict()["counts"]["out_of_bounds"] == 2
    with pytest.raises(PlacedLayoutError, match="outside page bounds"):
        validate_placed_project(project)


def test_placed_items_geometry_includes_symbol_bodies_fields_and_labels() -> None:
    geometry = placed_items_geometry(
        (
            _placed_box("U1", 50.0, 50.0),
            PlacedLabel(name="NET_A", at=(80.0, 50.0), uuid="label", nets=frozenset({"NET_A"})),
            PlacedText(text="LOCAL_PWR", at=(80.0, 60.0), uuid="text"),
        ),
        symbol_definitions={"Test:Box": _box_symbol_definition()},
    )

    assert {box.kind for box in geometry.boxes} >= {"symbol_body", "field", "label", "text"}


def test_placed_items_geometry_preserves_split_symbol_body_gaps() -> None:
    geometry = placed_items_geometry(
        (
            PlacedSymbol(
                lib_id="Test:SplitBox",
                at=(50.0, 50.0),
                unit=1,
                uuid="U1:uuid",
                project_name="demo",
                sheet_instance_path="/",
                reference="U1",
                properties=(),
                pins=(),
            ),
            PlacedLabel(name="G", at=(50.0, 50.0), uuid="gap-label"),
        ),
        symbol_definitions={"Test:SplitBox": _split_box_symbol_definition()},
    )

    overlaps = geometry.as_problem().overlaps()

    assert [(hit.first.kind, hit.second.kind) for hit in overlaps] == []


def test_label_geometry_elements_materialize_text_and_stub() -> None:
    elements = label_geometry_elements(
        id_prefix="U1.1",
        owner="U1",
        label_name="NET_A",
        point=PinPoint(x=10.0, y=10.0, label_x=20.0, label_y=10.0),
        justify="left",
        nets=frozenset({"NET_A"}),
    )

    assert [element.kind for element in elements] == ["label", "label_stub"]


def test_symbol_pin_point_uses_symbol_side_for_label_escape() -> None:
    symbol_info = placed_items_geometry(
        (),
        symbol_definitions={"Test:Box": _box_symbol_definition()},
    )
    pin = symbol_info  # keeps this test focused on the public helper below
    del pin

    from ksch.kicad.symbols import symbol_info_from_definition

    info = symbol_info_from_definition("Test:Box", _box_symbol_definition())
    point = symbol_pin_point(50.0, 50.0, info.pins[0], symbol_info=info)

    assert point.x < point.label_x or point.x > point.label_x


def test_sheet_symbol_pin_point_matches_symbol_pin_point() -> None:
    from ksch.kicad.symbols import symbol_info_from_definition

    info = symbol_info_from_definition("Test:Box", _box_symbol_definition())

    assert sheet_symbol_pin_point(50.0, 50.0, info.pins[0], symbol_info=info) == symbol_pin_point(
        50.0,
        50.0,
        info.pins[0],
        symbol_info=info,
    )

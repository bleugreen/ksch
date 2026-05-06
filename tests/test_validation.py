from pathlib import Path

import pytest

from ksch.layout import Point
from ksch.placed import (
    PlacedItem,
    PlacedLabel,
    PlacedProject,
    PlacedProperty,
    PlacedSheet,
    PlacedSymbol,
    PlacedWire,
)
from ksch.validation import PlacedLayoutError, placed_layout_problem, validate_placed_project


def _sheet(*items: PlacedItem) -> PlacedSheet:
    return PlacedSheet(
        path="/",
        filename=Path("demo.kicad_sch"),
        uuid="sheet",
        paper="A4",
        lib_symbols=(),
        items=items,
        instance_path="/",
        page="1",
    )


def test_placed_layout_problem_reports_cross_net_wire_contacts() -> None:
    problem = placed_layout_problem(
        _sheet(
            PlacedWire(
                start=(0, 10),
                end=(20, 10),
                uuid="wire-vdd",
                nets=frozenset({"VDD"}),
            ),
            PlacedWire(
                start=(10, 0),
                end=(10, 20),
                uuid="wire-gnd",
                nets=frozenset({"GND"}),
            ),
        )
    )

    contacts = problem.cross_net_contacts()

    assert [(hit.first.id, hit.second.id, hit.point) for hit in contacts] == [
        ("wire-vdd", "wire-gnd", Point(10, 10))
    ]


def test_validate_placed_project_rejects_cross_net_wire_contacts() -> None:
    project = PlacedProject(
        name="demo",
        sheets=(
            _sheet(
                PlacedWire(
                    start=(0, 10),
                    end=(20, 10),
                    uuid="wire-vdd",
                    nets=frozenset({"VDD"}),
                ),
                PlacedWire(
                    start=(10, 0),
                    end=(10, 20),
                    uuid="wire-gnd",
                    nets=frozenset({"GND"}),
                ),
            ),
        ),
    )

    with pytest.raises(PlacedLayoutError, match="cross-net wire contact"):
        validate_placed_project(project)


def test_validate_placed_project_rejects_endpoint_endpoint_cross_net_contacts() -> None:
    project = PlacedProject(
        name="demo",
        sheets=(
            _sheet(
                PlacedWire(
                    start=(10, 10),
                    end=(20, 10),
                    uuid="wire-a",
                    nets=frozenset({"A"}),
                    start_terminals=frozenset({"U1.A"}),
                ),
                PlacedWire(
                    start=(10, 10),
                    end=(10, 20),
                    uuid="wire-b",
                    nets=frozenset({"B"}),
                    start_terminals=frozenset({"U1.B"}),
                ),
            ),
        ),
    )

    with pytest.raises(PlacedLayoutError, match="near terminals U1.A, U1.B"):
        validate_placed_project(project)


def test_placed_layout_problem_treats_labels_as_text_blockers() -> None:
    problem = placed_layout_problem(
        _sheet(
            PlacedLabel(
                name="GND",
                at=(10, 10),
                uuid="label-gnd",
                nets=frozenset({"GND"}),
            )
        )
    )
    wire = placed_layout_problem(
        _sheet(
            PlacedWire(
                start=(0, 10),
                end=(30, 10),
                uuid="wire-vdd",
                nets=frozenset({"VDD"}),
            )
        )
    ).segments[0]

    assert [element.id for element in problem.blocking_elements(wire)] == ["label-gnd"]


def test_placed_layout_problem_includes_visible_symbol_fields() -> None:
    problem = placed_layout_problem(
        _sheet(
            PlacedSymbol(
                lib_id="Test:C",
                at=(20, 20),
                unit=1,
                uuid="symbol-c1",
                project_name="demo",
                sheet_instance_path="/",
                reference="C1",
                properties=(
                    PlacedProperty(name="Reference", value="C1", at=(10, 10)),
                    PlacedProperty(name="Footprint", value="", at=(10, 12), hidden=True),
                ),
            )
        )
    )

    assert [(element.id, element.owner, element.kind) for element in problem.elements] == [
        ("symbol-c1:Reference", "C1", "field")
    ]

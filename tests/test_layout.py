from ksch.layout import (
    ContactLink,
    LayoutNode,
    Point,
    Rect,
    layout_sheet_symbols,
    solve_contact_layout,
)


def test_layout_places_connectors_left_and_ics_center() -> None:
    positions = layout_sheet_symbols(["J1", "U2", "C1", "R1"])
    assert positions["J1"].x < positions["U2"].x
    assert positions["C1"].y > positions["U2"].y
    assert positions["R1"].y > positions["U2"].y


def test_layout_is_stable_for_same_refs() -> None:
    first = layout_sheet_symbols(["U2", "J1", "C1"])
    second = layout_sheet_symbols(["C1", "J1", "U2"])
    assert first == second
    assert isinstance(first["U2"], Point)


def test_contact_solver_pulls_to_preferred_boundary_without_overlap() -> None:
    nodes = {
        "U1": LayoutNode(
            id="U1",
            center=Point(100.0, 100.0),
            width=20.0,
            height=20.0,
            movable=False,
        ),
        "C1": LayoutNode(
            id="C1",
            center=Point(180.0, 100.0),
            width=8.0,
            height=8.0,
        ),
    }

    solved = solve_contact_layout(
        nodes,
        [ContactLink("C1", "U1", preferred_gap=6.0, strength=0.35)],
        bounds=Rect(0.0, 0.0, 220.0, 180.0),
        iterations=80,
        grid=0.01,
    )

    assert solved["C1"].center.x < 180.0
    assert solved["C1"].rect().left >= solved["U1"].rect().right
    assert round(solved["U1"].rect().gap_to(solved["C1"].rect()), 1) == 6.0


def test_contact_solver_finds_boundaries_between_neighbors() -> None:
    nodes = {
        "U1": LayoutNode(
            id="U1",
            center=Point(100.0, 100.0),
            width=20.0,
            height=20.0,
            movable=False,
        ),
        "R1": LayoutNode(id="R1", center=Point(180.0, 96.0), width=8.0, height=18.0),
        "R2": LayoutNode(id="R2", center=Point(180.0, 104.0), width=8.0, height=18.0),
    }

    solved = solve_contact_layout(
        nodes,
        [
            ContactLink("R1", "U1", preferred_gap=8.0, strength=0.3),
            ContactLink("R2", "U1", preferred_gap=8.0, strength=0.3),
        ],
        bounds=Rect(0.0, 0.0, 220.0, 180.0),
        iterations=100,
        grid=0.01,
    )

    assert solved["R1"].rect().gap_to(solved["U1"].rect()) <= 10.0
    assert solved["R2"].rect().gap_to(solved["U1"].rect()) <= 10.0
    assert not solved["R1"].rect().overlaps(solved["R2"].rect())


def test_contact_solver_does_not_clamp_fixed_nodes_to_bounds() -> None:
    nodes = {
        "J1": LayoutNode(
            id="J1",
            center=Point(40.0, 260.0),
            width=20.0,
            height=40.0,
            movable=False,
        ),
        "C1": LayoutNode(
            id="C1",
            center=Point(80.0, 160.0),
            width=8.0,
            height=8.0,
        ),
    }

    solved = solve_contact_layout(
        nodes,
        [ContactLink("C1", "J1", preferred_gap=8.0, strength=0.2)],
        bounds=Rect(0.0, 0.0, 120.0, 180.0),
        iterations=20,
        grid=0.01,
    )

    assert solved["J1"].center == Point(40.0, 260.0)
    assert solved["C1"].rect().bottom <= 180.0

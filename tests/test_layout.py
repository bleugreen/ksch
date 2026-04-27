from ksch.layout import Point, layout_sheet_symbols


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

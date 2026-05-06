from ksch.layout import Point, Rect
from ksch.layout_problem import LayoutElement, LayoutProblem, LayoutSegment, text_rect


def test_layout_problem_reports_overlapping_symbol_bodies() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1", owner="U1", kind="symbol", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="C1", owner="C1", kind="symbol", rect=Rect(10, 10, 14, 14)),
        )
    )

    overlaps = problem.overlaps()

    assert [(hit.first.id, hit.second.id) for hit in overlaps] == [("U1", "C1")]


def test_layout_problem_ignores_same_owner_overlaps() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1:body", owner="U1", kind="symbol", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="U1:value", owner="U1", kind="field", rect=Rect(5, 5, 10, 8)),
        )
    )

    assert problem.overlaps() == ()


def test_text_rect_tracks_left_and_right_justified_anchors() -> None:
    left = text_rect(Point(10, 20), "CM5_5V_IN", justify="left")
    right = text_rect(Point(10, 20), "CM5_5V_IN", justify="right")

    assert left.left == 10
    assert left.right > 10
    assert right.left < 10
    assert right.right == 10
    assert left.top < 20 < left.bottom


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

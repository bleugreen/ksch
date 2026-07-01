from ksch.layout import Rect
from ksch.segment_geometry import point_on_segment, segment_intersects_rect, segments_touch


def test_segments_touch_at_orthogonal_crossing() -> None:
    assert segments_touch((0.0, 5.0, 10.0, 5.0), (5.0, 0.0, 5.0, 10.0))


def test_segments_touch_for_collinear_overlap() -> None:
    assert segments_touch((0.0, 5.0, 10.0, 5.0), (7.5, 5.0, 20.0, 5.0))


def test_point_on_segment_ignores_off_segment_points() -> None:
    assert point_on_segment((5.0, 0.0), (0.0, 0.0, 10.0, 0.0))
    assert not point_on_segment((5.0, 1.0), (0.0, 0.0, 10.0, 0.0))


def test_segment_intersects_rect_for_crossing_path() -> None:
    rect = Rect(left=4.0, top=4.0, right=6.0, bottom=6.0)

    assert segment_intersects_rect((0.0, 5.0, 10.0, 5.0), rect)
    assert not segment_intersects_rect((0.0, 7.0, 10.0, 7.0), rect)

from ksch.layout import Point, Rect


def test_rects_that_overlap_report_negative_gap() -> None:
    first = Rect(left=0.0, top=0.0, right=10.0, bottom=10.0)
    second = Rect(left=7.0, top=2.0, right=12.0, bottom=6.0)

    assert first.overlaps(second)
    assert first.gap_to(second) == -3.0


def test_rects_that_touch_do_not_overlap() -> None:
    first = Rect(left=0.0, top=0.0, right=10.0, bottom=10.0)
    second = Rect(left=10.0, top=0.0, right=20.0, bottom=10.0)

    assert not first.overlaps(second)
    assert first.gap_to(second) == 0.0


def test_rect_translation_preserves_size_and_center() -> None:
    rect = Rect(left=1.0, top=2.0, right=5.0, bottom=8.0)

    moved = rect.translated(10.0, -2.0)

    assert moved.width == rect.width
    assert moved.height == rect.height
    assert moved.center == Point(13.0, 3.0)

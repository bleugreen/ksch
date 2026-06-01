from ksch.geometry import Coordinate, WireSegment
from ksch.layout import Rect

EPSILON = 0.001


def coordinate(value_x: float, value_y: float) -> Coordinate:
    return (round(value_x, 3), round(value_y, 3))


def _ranges_overlap(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> bool:
    return max(min(first_start, first_end), min(second_start, second_end)) <= min(
        max(first_start, first_end),
        max(second_start, second_end),
    ) + EPSILON


def _segment_bounds_overlap_rect(segment: WireSegment, rect: Rect) -> bool:
    return _ranges_overlap(segment[0], segment[2], rect.left, rect.right) and _ranges_overlap(
        segment[1],
        segment[3],
        rect.top,
        rect.bottom,
    )


def point_on_segment(point: Coordinate, segment: WireSegment) -> bool:
    point_x, point_y = point
    start_x, start_y, end_x, end_y = segment
    if abs(start_x - end_x) < EPSILON:
        return (
            abs(point_x - start_x) < EPSILON
            and min(start_y, end_y) - EPSILON <= point_y <= max(start_y, end_y) + EPSILON
        )
    if abs(start_y - end_y) < EPSILON:
        return (
            abs(point_y - start_y) < EPSILON
            and min(start_x, end_x) - EPSILON <= point_x <= max(start_x, end_x) + EPSILON
        )
    return False


def segments_touch(first: WireSegment, second: WireSegment) -> bool:
    first_start = coordinate(first[0], first[1])
    first_end = coordinate(first[2], first[3])
    second_start = coordinate(second[0], second[1])
    second_end = coordinate(second[2], second[3])
    first = (*first_start, *first_end)
    second = (*second_start, *second_end)

    first_vertical = abs(first[0] - first[2]) < EPSILON
    first_horizontal = abs(first[1] - first[3]) < EPSILON
    second_vertical = abs(second[0] - second[2]) < EPSILON
    second_horizontal = abs(second[1] - second[3]) < EPSILON

    if first_vertical and second_vertical:
        return abs(first[0] - second[0]) < EPSILON and not (
            max(first[1], first[3]) < min(second[1], second[3]) - EPSILON
            or max(second[1], second[3]) < min(first[1], first[3]) - EPSILON
        )
    if first_horizontal and second_horizontal:
        return abs(first[1] - second[1]) < EPSILON and not (
            max(first[0], first[2]) < min(second[0], second[2]) - EPSILON
            or max(second[0], second[2]) < min(first[0], first[2]) - EPSILON
        )
    if first_vertical and second_horizontal:
        return point_on_segment((first[0], second[1]), first) and point_on_segment(
            (first[0], second[1]),
            second,
        )
    if first_horizontal and second_vertical:
        return point_on_segment((second[0], first[1]), first) and point_on_segment(
            (second[0], first[1]),
            second,
        )
    return False


def segment_intersects_rect(segment: WireSegment, rect: Rect) -> bool:
    if not _segment_bounds_overlap_rect(segment, rect):
        return False

    start = coordinate(segment[0], segment[1])
    end = coordinate(segment[2], segment[3])
    if rect.left <= start[0] <= rect.right and rect.top <= start[1] <= rect.bottom:
        return True
    if rect.left <= end[0] <= rect.right and rect.top <= end[1] <= rect.bottom:
        return True

    rect_edges = (
        (rect.left, rect.top, rect.right, rect.top),
        (rect.right, rect.top, rect.right, rect.bottom),
        (rect.right, rect.bottom, rect.left, rect.bottom),
        (rect.left, rect.bottom, rect.left, rect.top),
    )
    return any(segments_touch(segment, edge) for edge in rect_edges)

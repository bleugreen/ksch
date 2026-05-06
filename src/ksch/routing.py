from ksch.geometry import Coordinate, PinPoint, WireSegment
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


def _segments_bounds_overlap(first: WireSegment, second: WireSegment) -> bool:
    return _ranges_overlap(first[0], first[2], second[0], second[2]) and _ranges_overlap(
        first[1],
        first[3],
        second[1],
        second[3],
    )


def _point_within_segment_bounds(point: Coordinate, segment: WireSegment) -> bool:
    return (
        min(segment[0], segment[2]) - EPSILON
        <= point[0]
        <= max(segment[0], segment[2]) + EPSILON
        and min(segment[1], segment[3]) - EPSILON
        <= point[1]
        <= max(segment[1], segment[3]) + EPSILON
    )


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


def without_zero_segments(segments: list[WireSegment]) -> list[WireSegment]:
    return [
        segment
        for segment in segments
        if abs(segment[0] - segment[2]) > EPSILON or abs(segment[1] - segment[3]) > EPSILON
    ]


def segment_endpoint_coordinates(segment: WireSegment) -> tuple[Coordinate, Coordinate]:
    return (coordinate(segment[0], segment[1]), coordinate(segment[2], segment[3]))


def split_segments_at_coordinates(
    segments: list[WireSegment],
    coordinates: set[Coordinate],
) -> list[WireSegment]:
    split_segments: list[WireSegment] = []
    for segment in segments:
        start_x, start_y, end_x, end_y = segment
        split_points = [
            coordinate
            for coordinate in coordinates
            if (
                _point_within_segment_bounds(coordinate, segment)
                and point_on_segment(coordinate, segment)
            )
        ]
        split_points.extend([coordinate(start_x, start_y), coordinate(end_x, end_y)])
        if abs(start_x - end_x) < EPSILON:
            sorted_points = sorted(set(split_points), key=lambda item: item[1])
        else:
            sorted_points = sorted(set(split_points), key=lambda item: item[0])
        for start, end in zip(sorted_points, sorted_points[1:], strict=False):
            split_segments.append((start[0], start[1], end[0], end[1]))
    return without_zero_segments(split_segments)


def canonical_segment_key(segment: WireSegment) -> tuple[Coordinate, Coordinate]:
    start = coordinate(segment[0], segment[1])
    end = coordinate(segment[2], segment[3])
    return (start, end) if start <= end else (end, start)


def normalize_wire_segments(segments: list[WireSegment]) -> list[WireSegment]:
    segments = without_zero_segments(segments)
    split_points = {
        endpoint
        for segment in segments
        for endpoint in segment_endpoint_coordinates(segment)
    }
    for segment in segments:
        split_points.update(
            point
            for other in segments
            for point in segment_endpoint_coordinates(other)
            if _point_within_segment_bounds(point, segment) and point_on_segment(point, segment)
        )

    split_segments = split_segments_at_coordinates(segments, split_points)
    normalized: list[WireSegment] = []
    seen: set[tuple[Coordinate, Coordinate]] = set()
    for segment in split_segments:
        key = canonical_segment_key(segment)
        if key in seen:
            continue
        seen.add(key)
        start, end = key
        normalized.append((start[0], start[1], end[0], end[1]))
    return normalized


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


def segments_clear_existing(
    segments: list[WireSegment],
    occupied_segments: list[WireSegment],
) -> bool:
    return not any(
        segments_touch(segment, occupied)
        for segment in segments
        for occupied in occupied_segments
        if _segments_bounds_overlap(segment, occupied)
    )


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


def segments_clear_rects(
    segments: list[WireSegment],
    blocked_rects: tuple[Rect, ...],
) -> bool:
    return not any(
        segment_intersects_rect(segment, rect)
        for segment in segments
        for rect in blocked_rects
    )


def pin_point_coordinates(points: list[PinPoint]) -> set[Coordinate]:
    coordinates: set[Coordinate] = set()
    for point in points:
        coordinates.add(coordinate(point.x, point.y))
        coordinates.add(coordinate(point.label_x, point.label_y))
    return coordinates


def pin_stub_segments(points: list[PinPoint]) -> list[WireSegment]:
    return [
        segment
        for point in points
        for segment in [
            None
            if point.x == point.label_x and point.y == point.label_y
            else (point.x, point.y, point.label_x, point.label_y)
        ]
        if segment is not None
    ]


def pin_point_obstacle_coordinates(points: list[PinPoint]) -> set[Coordinate]:
    return {
        point
        for pin in points
        for point in (
            coordinate(pin.x, pin.y),
            coordinate(pin.label_x, pin.label_y),
        )
    }


def segments_clear_obstacles(
    segments: list[WireSegment],
    *,
    obstacles: set[Coordinate],
    allowed: set[Coordinate],
) -> bool:
    blocked = obstacles - allowed
    for segment in segments:
        for obstacle in blocked:
            if _point_within_segment_bounds(obstacle, segment) and point_on_segment(
                obstacle,
                segment,
            ):
                return False
    return True

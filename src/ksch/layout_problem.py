from dataclasses import dataclass

from ksch.geometry import WireSegment
from ksch.layout import Point, Rect
from ksch.routing import point_on_segment, segment_intersects_rect, segments_touch


@dataclass(frozen=True)
class LayoutElement:
    id: str
    owner: str
    kind: str
    rect: Rect
    nets: frozenset[str] = frozenset()
    movable: bool = True


@dataclass(frozen=True)
class LayoutOverlap:
    first: LayoutElement
    second: LayoutElement


@dataclass(frozen=True)
class LayoutSegment:
    id: str
    owner: str
    kind: str
    start: Point
    end: Point
    nets: frozenset[str] = frozenset()
    start_terminals: frozenset[str] = frozenset()
    end_terminals: frozenset[str] = frozenset()

    def wire_segment(self) -> WireSegment:
        return (self.start.x, self.start.y, self.end.x, self.end.y)


@dataclass(frozen=True)
class LayoutContact:
    first: LayoutSegment
    second: LayoutSegment
    point: Point


@dataclass(frozen=True)
class LayoutProblem:
    elements: tuple[LayoutElement, ...] = ()
    segments: tuple[LayoutSegment, ...] = ()

    def overlaps(self) -> tuple[LayoutOverlap, ...]:
        overlaps: list[LayoutOverlap] = []
        for first_index, first in enumerate(self.elements):
            for second in self.elements[first_index + 1 :]:
                if first.owner == second.owner:
                    continue
                if first.rect.overlaps(second.rect):
                    overlaps.append(LayoutOverlap(first=first, second=second))
        return tuple(overlaps)

    def cross_net_contacts(self) -> tuple[LayoutContact, ...]:
        contacts: list[LayoutContact] = []
        for first_index, first in enumerate(self.segments):
            for second in self.segments[first_index + 1 :]:
                if first.owner == second.owner:
                    continue
                if not first.nets or not second.nets or first.nets & second.nets:
                    continue
                if not segments_touch(first.wire_segment(), second.wire_segment()):
                    continue
                contacts.append(
                    LayoutContact(
                        first=first,
                        second=second,
                        point=_segment_contact_point(first, second),
                    )
                )
        return tuple(contacts)

    def blocking_elements(self, segment: LayoutSegment) -> tuple[LayoutElement, ...]:
        return tuple(
            element
            for element in self.elements
            if element.owner != segment.owner and _segment_intersects_rect(segment, element.rect)
        )


def text_rect(
    anchor: Point,
    text: str,
    *,
    justify: str = "left",
    char_width: float = 1.27,
    half_height: float = 1.27,
) -> Rect:
    width = max(char_width, len(text) * char_width)
    if justify == "right":
        return Rect(
            left=anchor.x - width,
            top=anchor.y - half_height,
            right=anchor.x,
            bottom=anchor.y + half_height,
        )
    return Rect(
        left=anchor.x,
        top=anchor.y - half_height,
        right=anchor.x + width,
        bottom=anchor.y + half_height,
    )


def _segment_intersects_rect(segment: LayoutSegment, rect: Rect) -> bool:
    return segment_intersects_rect(segment.wire_segment(), rect)


def _segment_contact_point(first: LayoutSegment, second: LayoutSegment) -> Point:
    first_segment = first.wire_segment()
    second_segment = second.wire_segment()
    candidates = [
        first.start,
        first.end,
        second.start,
        second.end,
    ]
    for candidate in candidates:
        point = (candidate.x, candidate.y)
        if point_on_segment(point, first_segment) and point_on_segment(point, second_segment):
            return candidate

    if abs(first.start.x - first.end.x) < 0.001 and abs(second.start.y - second.end.y) < 0.001:
        return Point(first.start.x, second.start.y)
    if abs(first.start.y - first.end.y) < 0.001 and abs(second.start.x - second.end.x) < 0.001:
        return Point(second.start.x, first.start.y)

    return first.start

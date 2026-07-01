from dataclasses import dataclass
from typing import Any

from ksch.layout import GEOMETRY_EPSILON, Point, Rect, usable_page_rect_for_paper
from ksch.placed import (
    PlacedProject,
    PlacedSheet,
)
from ksch.schematic_geometry import (
    LayoutElement,
    LayoutContact,
    LayoutOverlap,
    LayoutProblem,
    LayoutSegment,
    placed_sheet_geometry,
)


@dataclass(frozen=True)
class PlacedLayoutContact:
    sheet_path: str
    contact: LayoutContact


@dataclass(frozen=True)
class PlacedRouteBlocker:
    sheet_path: str
    segment: LayoutSegment
    blocker: LayoutElement


@dataclass(frozen=True)
class PlacedOutOfBounds:
    sheet_path: str
    item: LayoutElement | LayoutSegment
    page_rect: Rect


@dataclass(frozen=True)
class PlacedLayoutReport:
    layout_errors: tuple[str, ...]
    out_of_bounds: tuple[PlacedOutOfBounds, ...]
    visible_overlaps: tuple[tuple[str, LayoutOverlap], ...]
    route_blockers: tuple[PlacedRouteBlocker, ...]
    cross_net_contacts: tuple[PlacedLayoutContact, ...]

    @property
    def is_legal(self) -> bool:
        return not (
            self.layout_errors
            or self.out_of_bounds
            or self.visible_overlaps
            or self.route_blockers
            or self.cross_net_contacts
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "legal": self.is_legal,
            "counts": {
                "layout_errors": len(self.layout_errors),
                "out_of_bounds": len(self.out_of_bounds),
                "visible_overlaps": len(self.visible_overlaps),
                "route_blockers": len(self.route_blockers),
                "cross_net_contacts": len(self.cross_net_contacts),
            },
            "layout_errors": list(self.layout_errors),
            "out_of_bounds": [
                {
                    "sheet_path": violation.sheet_path,
                    "item": _layout_item_dict(violation.item),
                    "page_rect": _rect_dict(violation.page_rect),
                }
                for violation in self.out_of_bounds
            ],
            "visible_overlaps": [
                {
                    "sheet_path": sheet_path,
                    "first": _element_dict(overlap.first),
                    "second": _element_dict(overlap.second),
                }
                for sheet_path, overlap in self.visible_overlaps
            ],
            "route_blockers": [
                {
                    "sheet_path": blocker.sheet_path,
                    "segment": _segment_dict(blocker.segment),
                    "blocker": _element_dict(blocker.blocker),
                }
                for blocker in self.route_blockers
            ],
            "cross_net_contacts": [
                {
                    "sheet_path": contact.sheet_path,
                    "first": _segment_dict(contact.contact.first),
                    "second": _segment_dict(contact.contact.second),
                    "point": _point_dict(contact.contact.point),
                }
                for contact in self.cross_net_contacts
            ],
        }


class PlacedLayoutError(ValueError):
    pass


def placed_layout_report(
    project: PlacedProject,
    *,
    layout_errors: tuple[str, ...] = (),
) -> PlacedLayoutReport:
    visible_overlaps: list[tuple[str, LayoutOverlap]] = []
    out_of_bounds: list[PlacedOutOfBounds] = []
    route_blockers_: list[PlacedRouteBlocker] = []
    cross_net_contacts_: list[PlacedLayoutContact] = []
    for sheet in project.sheets:
        geometry = placed_sheet_geometry(sheet)
        out_of_bounds.extend(_out_of_bounds(sheet, geometry))
        problem = geometry.as_problem()
        for overlap in problem.overlaps():
            visible_overlaps.append((sheet.path, overlap))
        for segment, blocker in geometry.route_blockers():
            route_blockers_.append(
                PlacedRouteBlocker(
                    sheet_path=sheet.path,
                    segment=segment,
                    blocker=blocker,
                )
            )
        for contact in problem.cross_net_contacts():
            cross_net_contacts_.append(
                PlacedLayoutContact(sheet_path=sheet.path, contact=contact)
            )
    return PlacedLayoutReport(
        layout_errors=layout_errors,
        out_of_bounds=tuple(out_of_bounds),
        visible_overlaps=tuple(visible_overlaps),
        route_blockers=tuple(route_blockers_),
        cross_net_contacts=tuple(cross_net_contacts_),
    )


def validate_placed_project(project: PlacedProject) -> None:
    report = placed_layout_report(project)
    bounds = report.out_of_bounds
    if bounds:
        first_bounds = bounds[0]
        raise PlacedLayoutError(
            "geometry outside page bounds in "
            f"{first_bounds.sheet_path}: {first_bounds.item.id} "
            f"({first_bounds.item.kind}) is outside {first_bounds.page_rect}"
        )
    overlaps = report.visible_overlaps
    if overlaps:
        sheet_path, overlap = overlaps[0]
        raise PlacedLayoutError(
            "visible geometry overlap in "
            f"{sheet_path}: {overlap.first.id} ({overlap.first.kind}) overlaps "
            f"{overlap.second.id} ({overlap.second.kind})"
        )
    blockers = report.route_blockers
    if blockers:
        first_blocker = blockers[0]
        raise PlacedLayoutError(
            "route blocker in "
            f"{first_blocker.sheet_path}: {first_blocker.segment.id} "
            f"({first_blocker.segment.kind}) crosses {first_blocker.blocker.id} "
            f"({first_blocker.blocker.kind})"
        )
    contacts = report.cross_net_contacts
    if not contacts:
        return
    first = contacts[0]
    raise PlacedLayoutError(
        "cross-net wire contact in "
        f"{first.sheet_path}: {first.contact.first.id} touches "
        f"{first.contact.second.id} at "
        f"({first.contact.point.x}, {first.contact.point.y})"
        f"{_terminal_detail(first.contact)}"
    )


def cross_net_contacts(project: PlacedProject) -> tuple[PlacedLayoutContact, ...]:
    contacts: list[PlacedLayoutContact] = []
    for sheet in project.sheets:
        for contact in placed_geometry_problem(sheet).cross_net_contacts():
            contacts.append(PlacedLayoutContact(sheet_path=sheet.path, contact=contact))
    return tuple(contacts)


def visible_geometry_overlaps(project: PlacedProject) -> tuple[tuple[str, LayoutOverlap], ...]:
    overlaps: list[tuple[str, LayoutOverlap]] = []
    for sheet in project.sheets:
        for overlap in placed_geometry_problem(sheet).overlaps():
            overlaps.append((sheet.path, overlap))
    return tuple(overlaps)


def route_blockers(project: PlacedProject) -> tuple[PlacedRouteBlocker, ...]:
    blockers: list[PlacedRouteBlocker] = []
    for sheet in project.sheets:
        for segment, blocker in placed_sheet_geometry(sheet).route_blockers():
            blockers.append(
                PlacedRouteBlocker(
                    sheet_path=sheet.path,
                    segment=segment,
                    blocker=blocker,
                )
            )
    return tuple(blockers)


def placed_geometry_problem(sheet: PlacedSheet) -> LayoutProblem:
    return placed_sheet_geometry(sheet).as_problem()


def _out_of_bounds(sheet: PlacedSheet, geometry: object) -> tuple[PlacedOutOfBounds, ...]:
    page_rect = usable_page_rect_for_paper(sheet.paper)
    if page_rect is None:
        return ()
    violations: list[PlacedOutOfBounds] = []
    for element in geometry.boxes:
        if not _rect_within(element.rect, page_rect):
            violations.append(
                PlacedOutOfBounds(
                    sheet_path=sheet.path,
                    item=element,
                    page_rect=page_rect,
                )
            )
    for segment in geometry.segments:
        if not (
            _point_within(segment.start, page_rect)
            and _point_within(segment.end, page_rect)
        ):
            violations.append(
                PlacedOutOfBounds(
                    sheet_path=sheet.path,
                    item=segment,
                    page_rect=page_rect,
                )
            )
    return tuple(violations)


def _rect_within(rect: Rect, bounds: Rect) -> bool:
    return (
        rect.left >= bounds.left - GEOMETRY_EPSILON
        and rect.top >= bounds.top - GEOMETRY_EPSILON
        and rect.right <= bounds.right + GEOMETRY_EPSILON
        and rect.bottom <= bounds.bottom + GEOMETRY_EPSILON
    )


def _point_within(point: Point, bounds: Rect) -> bool:
    return (
        bounds.left - GEOMETRY_EPSILON <= point.x <= bounds.right + GEOMETRY_EPSILON
        and bounds.top - GEOMETRY_EPSILON <= point.y <= bounds.bottom + GEOMETRY_EPSILON
    )


def _terminal_detail(contact: LayoutContact) -> str:
    first_terminals = _terminals_at(contact.point, contact.first)
    second_terminals = _terminals_at(contact.point, contact.second)
    terminals = sorted(first_terminals | second_terminals)
    if not terminals:
        return ""
    return f" near terminals {', '.join(terminals)}"


def _terminals_at(point: Point, segment: LayoutSegment) -> frozenset[str]:
    terminals: set[str] = set()
    if _same_point(point, segment.start):
        terminals.update(segment.start_terminals)
    if _same_point(point, segment.end):
        terminals.update(segment.end_terminals)
    return frozenset(terminals)


def _same_point(first: Point, second: Point) -> bool:
    return abs(first.x - second.x) < 0.001 and abs(first.y - second.y) < 0.001


def _element_dict(element: LayoutElement) -> dict[str, Any]:
    return {
        "id": element.id,
        "owner": element.owner,
        "kind": element.kind,
        "rect": _rect_dict(element.rect),
        "nets": sorted(element.nets),
        "movable": element.movable,
        "terminals": sorted(element.terminals),
    }


def _segment_dict(segment: LayoutSegment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "owner": segment.owner,
        "kind": segment.kind,
        "start": _point_dict(segment.start),
        "end": _point_dict(segment.end),
        "nets": sorted(segment.nets),
        "start_terminals": sorted(segment.start_terminals),
        "end_terminals": sorted(segment.end_terminals),
    }


def _layout_item_dict(item: LayoutElement | LayoutSegment) -> dict[str, Any]:
    if isinstance(item, LayoutElement):
        return _element_dict(item)
    return _segment_dict(item)


def _point_dict(point: Point) -> dict[str, float]:
    return {"x": point.x, "y": point.y}


def _rect_dict(rect: object) -> dict[str, float]:
    return {
        "left": getattr(rect, "left"),
        "top": getattr(rect, "top"),
        "right": getattr(rect, "right"),
        "bottom": getattr(rect, "bottom"),
    }

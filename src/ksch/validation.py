from collections.abc import Iterable
from dataclasses import dataclass

from ksch.layout import Point
from ksch.layout_problem import (
    LayoutContact,
    LayoutElement,
    LayoutProblem,
    LayoutSegment,
    text_rect,
)
from ksch.placed import (
    PlacedHierarchicalLabel,
    PlacedItem,
    PlacedLabel,
    PlacedProject,
    PlacedProperty,
    PlacedSheet,
    PlacedSheetBlock,
    PlacedSymbol,
    PlacedWire,
)


@dataclass(frozen=True)
class PlacedLayoutContact:
    sheet_path: str
    contact: LayoutContact


class PlacedLayoutError(ValueError):
    pass


def validate_placed_project(project: PlacedProject) -> None:
    contacts = cross_net_contacts(project)
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
        for contact in placed_layout_problem(sheet).cross_net_contacts():
            contacts.append(PlacedLayoutContact(sheet_path=sheet.path, contact=contact))
    return tuple(contacts)


def placed_layout_problem(sheet: PlacedSheet) -> LayoutProblem:
    return placed_items_layout_problem(sheet.items)


def placed_items_layout_problem(items: Iterable[PlacedItem]) -> LayoutProblem:
    elements: list[LayoutElement] = []
    segments: list[LayoutSegment] = []
    for item in items:
        if isinstance(item, PlacedWire):
            segments.append(_wire_segment(item))
        elif isinstance(item, PlacedLabel):
            if not item.hidden:
                elements.append(_label_element(item))
        elif isinstance(item, PlacedHierarchicalLabel):
            elements.append(
                _text_element(
                    id=item.uuid,
                    owner=item.uuid,
                    kind="hierarchical_label",
                    text=item.name,
                    at=item.at,
                    justify=item.justify,
                )
            )
        elif isinstance(item, PlacedSymbol):
            elements.extend(_symbol_property_elements(item))
        elif isinstance(item, PlacedSheetBlock):
            elements.extend(_sheet_block_property_elements(item))
    return LayoutProblem(elements=tuple(elements), segments=tuple(segments))


def _wire_segment(wire: PlacedWire) -> LayoutSegment:
    return LayoutSegment(
        id=wire.uuid,
        owner=_owner_from_nets(wire.nets, fallback=wire.uuid),
        kind="wire",
        start=Point(wire.start[0], wire.start[1]),
        end=Point(wire.end[0], wire.end[1]),
        nets=wire.nets,
        start_terminals=wire.start_terminals,
        end_terminals=wire.end_terminals,
    )


def _label_element(label: PlacedLabel) -> LayoutElement:
    return _text_element(
        id=label.uuid,
        owner=_owner_from_nets(label.nets, fallback=label.uuid),
        kind="label",
        text=label.name,
        at=label.at,
        justify=label.justify,
        nets=label.nets,
    )


def _symbol_property_elements(symbol: PlacedSymbol) -> list[LayoutElement]:
    return [
        _property_element(
            property_,
            id=f"{symbol.uuid}:{property_.name}",
            owner=symbol.reference,
        )
        for property_ in symbol.properties
        if not property_.hidden
    ]


def _sheet_block_property_elements(sheet: PlacedSheetBlock) -> list[LayoutElement]:
    return [
        _text_element(
            id=f"{sheet.uuid}:Sheetname",
            owner=sheet.uuid,
            kind="sheet_property",
            text=sheet.sheet_name,
            at=sheet.sheet_name_at,
            justify="left",
        ),
        _text_element(
            id=f"{sheet.uuid}:Sheetfile",
            owner=sheet.uuid,
            kind="sheet_property",
            text=sheet.sheet_file,
            at=sheet.sheet_file_at,
            justify="left",
        ),
    ]


def _property_element(
    property_: PlacedProperty,
    *,
    id: str,
    owner: str,
) -> LayoutElement:
    return _text_element(
        id=id,
        owner=owner,
        kind="field",
        text=property_.value,
        at=property_.at,
        justify=property_.justify,
    )


def _text_element(
    *,
    id: str,
    owner: str,
    kind: str,
    text: str,
    at: tuple[float, float],
    justify: str,
    nets: frozenset[str] = frozenset(),
) -> LayoutElement:
    return LayoutElement(
        id=id,
        owner=owner,
        kind=kind,
        rect=text_rect(Point(at[0], at[1]), text, justify=justify),
        nets=nets,
        movable=False,
    )


def _owner_from_nets(nets: frozenset[str], *, fallback: str) -> str:
    if len(nets) == 1:
        return next(iter(nets))
    return fallback


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

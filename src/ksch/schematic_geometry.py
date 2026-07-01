from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal

from ksch.geometry import (
    PinPoint,
    Rect as TupleRect,
    WireSegment,
    sexpr_point,
    symbol_body_rect,
    symbol_pin_coordinate,
)
from ksch.kicad.sexpr import atom
from ksch.kicad.symbols import SymbolInfo, SymbolPin, symbol_info_from_definition
from ksch.layout import GEOMETRY_EPSILON, Point, Rect, snap_grid
from ksch.placed import (
    PlacedHierarchicalLabel,
    PlacedItem,
    PlacedJunction,
    PlacedLabel,
    PlacedNoConnect,
    PlacedProperty,
    PlacedSheet,
    PlacedSheetBlock,
    PlacedSymbol,
    PlacedText,
    PlacedWire,
)
from ksch.segment_geometry import point_on_segment, segment_intersects_rect, segments_touch

SCHEMATIC_GRID = 2.54
PIN_LABEL_STUB = 5.08
FIELD_CLEARANCE = 5.08


@dataclass(frozen=True)
class LayoutElement:
    id: str
    owner: str
    kind: str
    rect: Rect
    nets: frozenset[str] = frozenset()
    movable: bool = True
    terminals: frozenset[str] = frozenset()


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
                if _allowed_element_overlap(first, second):
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
            if element.owner != segment.owner
            and segment_intersects_rect(segment.wire_segment(), element.rect)
        )


def text_rect(
    anchor: Point,
    text: str,
    *,
    justify: str = "left",
    rotation: int = 0,
    char_width: float = 1.27,
    half_height: float = 1.27,
) -> Rect:
    width = max(char_width, len(text) * char_width)
    if rotation % 180 != 0:
        if justify == "right":
            return Rect(
                left=anchor.x - half_height,
                top=anchor.y,
                right=anchor.x + half_height,
                bottom=anchor.y + width,
            )
        return Rect(
            left=anchor.x - half_height,
            top=anchor.y - width,
            right=anchor.x + half_height,
            bottom=anchor.y,
        )
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


@dataclass(frozen=True)
class SchematicGeometry:
    boxes: tuple[LayoutElement, ...] = ()
    segments: tuple[LayoutSegment, ...] = ()

    def as_problem(self) -> LayoutProblem:
        return LayoutProblem(elements=self.boxes, segments=self.segments)

    def route_blockers(self) -> tuple[tuple[LayoutSegment, LayoutElement], ...]:
        blockers: list[tuple[LayoutSegment, LayoutElement]] = []
        for segment in self.segments:
            for box in self.boxes:
                if segment_blocked_by_element(segment, box):
                    blockers.append((segment, box))
        return tuple(blockers)


@dataclass(frozen=True)
class _SymbolPropertyPoints:
    reference: Point
    value: Point
    footprint: Point
    justify: Literal["left", "right"] = "left"


@dataclass(frozen=True)
class GeometryProblem:
    geometry: SchematicGeometry = SchematicGeometry()

    @property
    def boxes(self) -> tuple[LayoutElement, ...]:
        return self.geometry.boxes

    @property
    def segments(self) -> tuple[LayoutSegment, ...]:
        return self.geometry.segments


def _allowed_element_overlap(first: LayoutElement, second: LayoutElement) -> bool:
    if first.id == second.id:
        return True
    if first.owner == second.owner and first.kind == second.kind == "symbol_body":
        return True
    if first.owner == second.owner and (
        (first.kind.startswith("pin_") and second.kind == "symbol_body")
        or (second.kind.startswith("pin_") and first.kind == "symbol_body")
    ):
        return True
    if first.owner == second.owner and first.kind.startswith("pin_") and second.kind.startswith("pin_"):
        return True
    kinds = {first.kind, second.kind}
    if "no_connect" in kinds and any(kind.startswith("pin_") for kind in kinds):
        return _terminal_sets_related(first.terminals, second.terminals)
    return kinds == {"no_connect", "symbol_body"}


def _is_zero_length_label_anchor(segment: LayoutSegment) -> bool:
    if segment.kind not in {"label_anchor", "hierarchical_label_anchor"}:
        return False
    return _same_layout_point(segment.start, segment.end)


def segment_blocked_by_element(segment: LayoutSegment, box: LayoutElement) -> bool:
    if _is_zero_length_label_anchor(segment):
        return False
    if box.kind == "no_connect":
        return False
    if box.owner == segment.owner:
        return False
    if _segment_has_terminal_for_box(segment, box):
        return False
    if box.nets and segment.nets and box.nets & segment.nets:
        return False
    return _segment_hits_rect_beyond_anchor(segment.wire_segment(), box.rect)


def _same_layout_point(first: Point, second: Point) -> bool:
    return (
        abs(first.x - second.x) < GEOMETRY_EPSILON
        and abs(first.y - second.y) < GEOMETRY_EPSILON
    )


def _segment_has_terminal_for_box(segment: LayoutSegment, box: LayoutElement) -> bool:
    terminals = (*segment.start_terminals, *segment.end_terminals)
    if box.terminals:
        return _terminal_sets_related(box.terminals, terminals)
    if box.kind == "pin_number":
        return any(_terminal_ref(terminal) == box.owner for terminal in terminals)
    return False


def _terminal_sets_related(first: Iterable[str], second: Iterable[str]) -> bool:
    return any(_terminals_related(left, right) for left in first for right in second)


def _terminals_related(first: str, second: str) -> bool:
    if first == second:
        return True
    first_ref = _terminal_ref(first)
    second_ref = _terminal_ref(second)
    if first_ref != second_ref:
        return False
    first_number = _terminal_pin_number(first)
    second_number = _terminal_pin_number(second)
    if first_number and second_number and first_number == second_number:
        return True
    return bool(first_number or second_number)


def _terminal_pin_number(terminal: str) -> str | None:
    if "@" in terminal:
        return terminal.rsplit("@", 1)[1] or None
    _ref, separator, pin = terminal.rpartition(".")
    return pin if separator else None


def _terminal_ref(terminal: str) -> str:
    ref, separator, _pin = terminal.partition(".")
    return ref if separator else terminal


def symbol_pin_point(
    symbol_x: float,
    symbol_y: float,
    pin: SymbolPin,
    *,
    symbol_info: SymbolInfo | None = None,
    symbol_rotation: int = 0,
    stub: float = PIN_LABEL_STUB,
) -> PinPoint:
    x, y = symbol_pin_coordinate(symbol_x, symbol_y, pin, symbol_rotation=symbol_rotation)
    side = _pin_side(symbol_info, pin, symbol_rotation=symbol_rotation)
    if side == "left":
        return PinPoint(x=x, y=y, label_x=x - stub, label_y=y)
    if side == "right":
        return PinPoint(x=x, y=y, label_x=x + stub, label_y=y)
    if side == "top":
        return PinPoint(x=x, y=y, label_x=x, label_y=y - stub)
    return PinPoint(x=x, y=y, label_x=x, label_y=y + stub)


def sheet_symbol_pin_point(
    symbol_x: float,
    symbol_y: float,
    pin: SymbolPin,
    *,
    symbol_info: SymbolInfo | None = None,
    symbol_rotation: int = 0,
) -> PinPoint:
    return symbol_pin_point(
        symbol_x,
        symbol_y,
        pin,
        symbol_info=symbol_info,
        symbol_rotation=symbol_rotation,
    )


def symbol_pin_side(
    symbol_info: SymbolInfo | None,
    pin: SymbolPin,
    *,
    symbol_rotation: int = 0,
) -> Literal["left", "right", "top", "bottom"]:
    return _pin_side(symbol_info, pin, symbol_rotation=symbol_rotation)


def symbol_property_points(
    symbol_x: float,
    symbol_y: float,
    symbol_info: SymbolInfo | None,
    *,
    ref: str,
    symbol_rotation: int = 0,
) -> _SymbolPropertyPoints:
    rect = _symbol_body_box(symbol_info, symbol_x, symbol_y, margin=1.27, symbol_rotation=symbol_rotation)
    if _uses_side_properties(symbol_info, symbol_rotation=symbol_rotation):
        x = _snap_grid(rect.left - FIELD_CLEARANCE)
        center_y = (rect.top + rect.bottom) / 2
        return _SymbolPropertyPoints(
            reference=Point(x, _snap_grid(center_y - 1.27)),
            value=Point(x, _snap_grid(center_y + 1.27)),
            footprint=Point(x, _snap_grid(rect.bottom + 3.81)),
            justify="right",
        )
    justify: Literal["left", "right"] = "right"
    x = rect.left - FIELD_CLEARANCE
    return _SymbolPropertyPoints(
        reference=Point(_snap_grid(x), _snap_grid(rect.top - 6.35)),
        value=Point(_snap_grid(x), _snap_grid(rect.top - 3.81)),
        footprint=Point(_snap_grid(x), _snap_grid(rect.bottom + 3.81)),
        justify=justify,
    )


def compact_symbol_property_points(
    symbol_x: float,
    symbol_y: float,
    symbol_info: SymbolInfo | None,
    *,
    ref: str,
    value: str,
    symbol_rotation: int = 0,
) -> _SymbolPropertyPoints | None:
    if not ref.startswith(("C", "Y", "X")):
        return None
    if symbol_info is None:
        return None
    pins = [pin for pin in symbol_info.pins if pin.at is not None and pin.unit in {0, 1}]
    if len(pins) < 2 or len(pins) > 3:
        return None

    body = _symbol_body_box(symbol_info, symbol_x, symbol_y, margin=0.64, symbol_rotation=symbol_rotation)
    sides = {_pin_side(symbol_info, pin, symbol_rotation=symbol_rotation) for pin in pins}
    center_x = (body.left + body.right) / 2
    center_y = (body.top + body.bottom) / 2

    if ref.startswith(("Y", "X")):
        return _SymbolPropertyPoints(
            reference=Point(_snap_grid(body.left - SCHEMATIC_GRID / 2), _snap_grid(center_y - SCHEMATIC_GRID / 2)),
            value=Point(_snap_grid(body.left - SCHEMATIC_GRID / 2), _snap_grid(center_y + SCHEMATIC_GRID / 2)),
            footprint=Point(_snap_grid(body.left - SCHEMATIC_GRID), _snap_grid(body.bottom + SCHEMATIC_GRID)),
            justify="right",
        )

    if "left" in sides and "right" in sides and not ({"top", "bottom"} & sides):
        return _SymbolPropertyPoints(
            reference=Point(_centered_right_anchor_x(ref, center_x), _snap_grid(body.top - 2 * SCHEMATIC_GRID)),
            value=Point(_centered_right_anchor_x(value, center_x), _snap_grid(body.top - SCHEMATIC_GRID)),
            footprint=Point(_snap_grid(body.left - SCHEMATIC_GRID), _snap_grid(body.bottom + SCHEMATIC_GRID)),
            justify="right",
        )

    return _SymbolPropertyPoints(
        reference=Point(_snap_grid(body.left - SCHEMATIC_GRID / 2), _snap_grid(center_y - SCHEMATIC_GRID / 2)),
        value=Point(_snap_grid(body.left - SCHEMATIC_GRID / 2), _snap_grid(center_y + SCHEMATIC_GRID / 2)),
        footprint=Point(_snap_grid(body.left - SCHEMATIC_GRID), _snap_grid(body.bottom + SCHEMATIC_GRID)),
        justify="right",
    )


def _centered_right_anchor_x(text: str, center_x: float) -> float:
    width = max(1.27, len(text) * 1.27)
    return _snap_grid(center_x + width / 2)


def _uses_side_properties(
    symbol_info: SymbolInfo | None,
    *,
    symbol_rotation: int = 0,
) -> bool:
    if symbol_info is None:
        return False
    pins = [pin for pin in symbol_info.pins if pin.at is not None and pin.unit in {0, 1}]
    if len(pins) != 2:
        return False
    sides = {_pin_side(symbol_info, pin, symbol_rotation=symbol_rotation) for pin in pins}
    return sides == {"top", "bottom"}


def resolved_symbol_readability_rects(
    project: object,
    sheet_path: str,
    ref: str,
    unit: int,
    position: Point,
    *,
    margin: float = 1.27,
    symbol_rotation: int = 0,
) -> list[Rect]:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols.get(ref)
    symbol_info = None
    if symbol_decl is not None:
        symbol_info = project.symbol_library.get(symbol_decl.lib)
    if symbol_info is not None:
        symbol_info = _unit_symbol_info(symbol_info, unit)
    body = _symbol_body_box(
        symbol_info,
        position.x,
        position.y,
        margin=margin,
        symbol_rotation=symbol_rotation,
    )
    props = symbol_property_points(
        position.x,
        position.y,
        symbol_info,
        ref=ref,
        symbol_rotation=symbol_rotation,
    )
    return [
        body,
        text_rect(props.reference, ref, justify=props.justify),
        text_rect(
            props.value,
            symbol_decl.value or ref if symbol_decl is not None else ref,
            justify=props.justify,
        ),
    ]


def resolved_symbol_readability_elements(
    project: object,
    sheet_path: str,
    ref: str,
    unit: int,
    position: Point,
    *,
    symbol_rotation: int = 0,
) -> tuple[LayoutElement, ...]:
    return tuple(
        LayoutElement(
            id=f"{ref}:{unit}:readability:{index}",
            owner=ref,
            kind="symbol_body" if index == 0 else "field",
            rect=rect,
        )
        for index, rect in enumerate(
            resolved_symbol_readability_rects(
                project,
                sheet_path,
                ref,
                unit,
                position,
                symbol_rotation=symbol_rotation,
            )
        )
    )


def placed_sheet_geometry(sheet: PlacedSheet) -> SchematicGeometry:
    symbol_definitions = {
        str(definition[1]): definition
        for definition in sheet.lib_symbols
        if len(definition) > 1
    }
    return placed_items_geometry(sheet.items, symbol_definitions=symbol_definitions)


def placed_items_geometry(
    items: Iterable[PlacedItem],
    *,
    symbol_library: dict[str, SymbolInfo] | None = None,
    symbol_definitions: dict[str, list[object]] | None = None,
) -> SchematicGeometry:
    library = dict(symbol_library or {})
    for lib_id, definition in (symbol_definitions or {}).items():
        if lib_id not in library:
            library[lib_id] = symbol_info_from_definition(lib_id, definition)

    boxes: list[LayoutElement] = []
    segments: list[LayoutSegment] = []
    for item in items:
        if isinstance(item, PlacedSymbol):
            symbol_info = library.get(item.lib_id)
            unit_info = _unit_symbol_info(symbol_info, item.unit)
            unit_pins = [
                pin
                for pin in (unit_info.pins if unit_info is not None else [])
                if pin.at is not None
            ]
            if item.lib_id not in {"power:KSCH_POWER_PORT", "power:KSCH_POWER_DRIVER"}:
                body_terminals = (
                    frozenset({f"{item.reference}.{unit_pins[0].name}@{unit_pins[0].number}"})
                    if len(unit_pins) == 1
                    else frozenset()
                )
                for index, body in enumerate(
                    _symbol_body_boxes(
                        symbol_info,
                        item.at[0],
                        item.at[1],
                        margin=1.27,
                        symbol_rotation=item.rotation,
                    )
                ):
                    suffix = "body" if index == 0 else f"body:{index}"
                    boxes.append(
                        LayoutElement(
                            id=f"{item.uuid}:{suffix}",
                            owner=item.reference,
                            kind="symbol_body",
                            rect=body,
                            terminals=body_terminals,
                        )
                    )
                for pin_text in _symbol_pin_text_boxes(
                    symbol_info,
                    item.at[0],
                    item.at[1],
                    item.unit,
                    item.reference,
                    symbol_rotation=item.rotation,
                ):
                    boxes.append(
                        LayoutElement(
                            id=f"{item.uuid}:{pin_text[0]}:{pin_text[1]}",
                            owner=item.reference,
                            kind=pin_text[0],
                            rect=pin_text[2],
                            terminals=frozenset({pin_text[3]}),
                        )
                    )
            for property_ in item.properties:
                if property_.hidden:
                    continue
                boxes.append(_property_box(item, property_))
        elif isinstance(item, PlacedSheetBlock):
            x, y = item.at
            width, height = item.size
            boxes.append(
                LayoutElement(
                    id=f"{item.uuid}:body",
                    owner=item.sheet_name,
                    kind="sheet_body",
                    rect=Rect(left=x, top=y, right=x + width, bottom=y + height),
                )
            )
            boxes.append(
                LayoutElement(
                    id=f"{item.uuid}:sheet-name",
                    owner=item.sheet_name,
                    kind="sheet_property",
                    rect=text_rect(Point(*item.sheet_name_at), item.sheet_name),
                )
            )
            boxes.append(
                LayoutElement(
                    id=f"{item.uuid}:sheet-file",
                    owner=item.sheet_name,
                    kind="sheet_property",
                    rect=text_rect(Point(*item.sheet_file_at), item.sheet_file),
                )
            )
        elif isinstance(item, PlacedWire):
            segments.append(
                _layout_segment(
                    id=item.uuid,
                    owner=_owner_from_nets(item.nets, default_owner=item.uuid),
                    kind="wire",
                    segment=(item.start[0], item.start[1], item.end[0], item.end[1]),
                    nets=item.nets,
                    start_terminals=item.start_terminals,
                    end_terminals=item.end_terminals,
                )
            )
        elif isinstance(item, PlacedJunction):
            boxes.append(
                LayoutElement(
                    id=item.uuid,
                    owner=_owner_from_nets(item.nets, default_owner=item.uuid),
                    kind="junction",
                    rect=Rect(
                        left=item.at[0] - 0.75,
                        top=item.at[1] - 0.75,
                        right=item.at[0] + 0.75,
                        bottom=item.at[1] + 0.75,
                    ),
                    nets=item.nets,
                )
            )
        elif isinstance(item, PlacedLabel):
            anchor = Point(item.at[0], item.at[1])
            boxes.append(
                LayoutElement(
                    id=item.uuid,
                    owner=_owner_from_nets(item.nets, default_owner=item.uuid),
                    kind="label",
                    rect=text_rect(
                        anchor,
                        item.name,
                        justify=item.justify,
                        rotation=item.rotation,
                    ),
                    nets=item.nets,
                )
            )
            segments.append(
                _layout_segment(
                    id=f"{item.uuid}:anchor",
                    owner=_owner_from_nets(item.nets, default_owner=item.uuid),
                    kind="label_anchor",
                    segment=(item.at[0], item.at[1], item.at[0], item.at[1]),
                    nets=item.nets,
                )
            )
        elif isinstance(item, PlacedHierarchicalLabel):
            anchor = Point(item.at[0], item.at[1])
            nets = frozenset({item.name})
            boxes.append(
                LayoutElement(
                    id=item.uuid,
                    owner=item.name,
                    kind="hierarchical_label",
                    rect=text_rect(
                        anchor,
                        item.name,
                        justify=item.justify,
                        rotation=item.rotation,
                    ),
                    nets=nets,
                )
            )
            segments.append(
                _layout_segment(
                    id=f"{item.uuid}:anchor",
                    owner=item.name,
                    kind="hierarchical_label_anchor",
                    segment=(item.at[0], item.at[1], item.at[0], item.at[1]),
                    nets=nets,
                )
            )
        elif isinstance(item, PlacedText):
            boxes.append(
                LayoutElement(
                    id=item.uuid,
                    owner=item.uuid,
                    kind="text",
                    rect=text_rect(
                        Point(item.at[0], item.at[1]),
                        item.text,
                        justify=item.justify,
                        rotation=item.rotation,
                    ),
                    nets=frozenset(),
                )
            )
        elif isinstance(item, PlacedNoConnect):
            boxes.append(
                LayoutElement(
                    id=item.uuid,
                    owner=item.terminal or item.uuid,
                    kind="no_connect",
                    rect=Rect(
                        left=item.at[0] - 1.27,
                        top=item.at[1] - 1.27,
                        right=item.at[0] + 1.27,
                        bottom=item.at[1] + 1.27,
                    ),
                    terminals=frozenset({item.terminal}) if item.terminal else frozenset(),
                )
            )
    return SchematicGeometry(boxes=tuple(boxes), segments=tuple(segments))


def legalize_sheet_geometry(
    items: Iterable[PlacedItem],
    *,
    symbol_library: dict[str, SymbolInfo] | None = None,
    symbol_definitions: dict[str, list[object]] | None = None,
) -> tuple[PlacedItem, ...]:
    # Placement owns geometry before emit; this gate rejects invalid geometry
    # that made it through assembly.
    item_tuple = tuple(items)
    geometry = placed_items_geometry(
        item_tuple,
        symbol_library=symbol_library,
        symbol_definitions=symbol_definitions,
    )
    overlaps = geometry.as_problem().overlaps()
    if overlaps:
        first = overlaps[0]
        raise ValueError(
            "illegal schematic geometry: "
            f"{first.first.id} ({first.first.owner} {first.first.kind}) overlaps "
            f"{first.second.id} ({first.second.owner} {first.second.kind}); "
            f"rects={first.first.rect} / {first.second.rect}"
        )
    route_blockers = geometry.route_blockers()
    if route_blockers:
        segment, blocker = route_blockers[0]
        raise ValueError(
            "illegal schematic geometry: "
            f"{segment.id} ({segment.owner} {segment.kind}) crosses "
            f"{blocker.id} ({blocker.owner} {blocker.kind}); "
            f"segment={segment.wire_segment()} rect={blocker.rect}"
        )
    return item_tuple


def label_geometry_elements(
    *,
    id_prefix: str,
    owner: str,
    label_name: str,
    point: PinPoint,
    justify: Literal["left", "right"],
    nets: frozenset[str],
    side: Literal["left", "right", "top", "bottom"] | None = None,
    stub_margin: float = 0.2,
) -> tuple[LayoutElement, ...]:
    elements = [
        LayoutElement(
            id=f"{id_prefix}:label",
            owner=owner,
            kind="label",
            rect=text_rect(
                Point(point.label_x, point.label_y),
                label_name,
                justify=justify,
            ),
            nets=nets,
        )
    ]
    stubs = point_stub_segments(point, side=side)
    for index, stub in enumerate(stubs):
        elements.append(
            LayoutElement(
                id=f"{id_prefix}:label-stub:{index}",
                owner=owner,
                kind="label_stub",
                rect=_segment_rect(stub, margin=stub_margin),
                nets=nets,
            )
        )
    return tuple(elements)


def point_stub_segment(point: PinPoint) -> WireSegment | None:
    segments = point_stub_segments(point)
    return segments[0] if len(segments) == 1 else None


def point_stub_segments(
    point: PinPoint,
    *,
    side: Literal["left", "right", "top", "bottom"] | None = None,
) -> tuple[WireSegment, ...]:
    if abs(point.x - point.label_x) < 0.001 and abs(point.y - point.label_y) < 0.001:
        return ()
    if abs(point.x - point.label_x) < 0.001 or abs(point.y - point.label_y) < 0.001:
        return ((point.x, point.y, point.label_x, point.label_y),)
    if side in {"left", "right"}:
        corner = (point.label_x, point.y)
    else:
        corner = (point.x, point.label_y)
    return (
        (point.x, point.y, corner[0], corner[1]),
        (corner[0], corner[1], point.label_x, point.label_y),
    )


def _segment_rect(segment: WireSegment, *, margin: float) -> Rect:
    return Rect(
        left=min(segment[0], segment[2]) - margin,
        top=min(segment[1], segment[3]) - margin,
        right=max(segment[0], segment[2]) + margin,
        bottom=max(segment[1], segment[3]) + margin,
    )


def _layout_segment(
    *,
    id: str,
    owner: str,
    kind: str,
    segment: WireSegment,
    nets: frozenset[str] = frozenset(),
    start_terminals: frozenset[str] = frozenset(),
    end_terminals: frozenset[str] = frozenset(),
) -> LayoutSegment:
    return LayoutSegment(
        id=id,
        owner=owner,
        kind=kind,
        start=Point(segment[0], segment[1]),
        end=Point(segment[2], segment[3]),
        nets=nets,
        start_terminals=start_terminals,
        end_terminals=end_terminals,
    )


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
        if point_on_segment(point, first_segment) and point_on_segment(
            point,
            second_segment,
        ):
            return candidate

    if (
        abs(first.start.x - first.end.x) < 0.001
        and abs(second.start.y - second.end.y) < 0.001
    ):
        return Point(first.start.x, second.start.y)
    if (
        abs(first.start.y - first.end.y) < 0.001
        and abs(second.start.x - second.end.x) < 0.001
    ):
        return Point(second.start.x, first.start.y)

    return first.start


def _property_box(symbol: PlacedSymbol, property_: PlacedProperty) -> LayoutElement:
    if symbol.rotation % 180 == 0:
        effective_rotation = property_.rotation
    else:
        effective_rotation = (symbol.rotation + property_.rotation) % 360
    return LayoutElement(
        id=f"{symbol.uuid}:{property_.name}",
        owner=symbol.reference,
        kind="field",
        rect=text_rect(
            Point(property_.at[0], property_.at[1]),
            property_.value,
            justify=property_.justify,
            rotation=effective_rotation,
        ),
    )


def _symbol_pin_text_boxes(
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
    unit: int,
    reference: str,
    *,
    symbol_rotation: int = 0,
) -> tuple[tuple[str, str, Rect, str], ...]:
    unit_info = _unit_symbol_info(symbol_info, unit)
    if unit_info is None:
        return ()
    boxes: list[tuple[str, str, Rect, str]] = []
    seen: set[str] = set()
    for pin in unit_info.pins:
        if pin.at is None or pin.number in seen:
            continue
        seen.add(pin.number)
        rotation = (int(pin.at[2]) + symbol_rotation) % 360
        pin_point = Point(*symbol_pin_coordinate(x, y, pin, symbol_rotation=symbol_rotation))
        direction = _pin_text_direction(rotation)
        name_anchor = Point(
            pin_point.x + direction[0] * (pin.length + 0.5),
            pin_point.y + direction[1] * (pin.length + 0.5),
        )
        name_rotation = 0 if rotation in {0, 180} else rotation
        name_justify: Literal["left", "right"] = "left"
        if rotation == 180:
            name_justify = "right"
        elif rotation == 90:
            name_justify = "right"
        terminal = f"{reference}.{pin.name}@{pin.number}"
        boxes.append(
            (
                "pin_name",
                pin.number,
                text_rect(
                    name_anchor,
                    pin.name,
                    justify=name_justify,
                    rotation=name_rotation,
                    char_width=0.9,
                    half_height=1.0,
                ),
                terminal,
            )
        )
        number_center = Point(
            pin_point.x + direction[0] * (pin.length / 2),
            pin_point.y + direction[1] * (pin.length / 2),
        )
        boxes.append(
            (
                "pin_number",
                pin.number,
                _centered_text_rect(number_center, pin.number, rotation=name_rotation),
                terminal,
            )
        )
    return tuple(boxes)


def _pin_text_direction(rotation: int) -> tuple[float, float]:
    rotation %= 360
    if rotation == 0:
        return (1.0, 0.0)
    if rotation == 180:
        return (-1.0, 0.0)
    if rotation == 90:
        return (0.0, -1.0)
    return (0.0, 1.0)


def _centered_text_rect(
    center: Point,
    text: str,
    *,
    rotation: int = 0,
    char_width: float = 0.9,
    half_height: float = 1.0,
) -> Rect:
    width = max(char_width, len(text) * char_width)
    if rotation % 180 != 0:
        return Rect(
            left=center.x - half_height,
            top=center.y - width / 2,
            right=center.x + half_height,
            bottom=center.y + width / 2,
        )
    return Rect(
        left=center.x - width / 2,
        top=center.y - half_height,
        right=center.x + width / 2,
        bottom=center.y + half_height,
    )


def _unit_symbol_info(symbol_info: SymbolInfo | None, unit: int) -> SymbolInfo | None:
    if symbol_info is None:
        return None
    return replace(
        symbol_info,
        pins=[
            replace(pin, unit=1)
            for pin in symbol_info.pins
            if pin.unit in {0, unit}
        ],
    )


def _pin_side(
    symbol_info: SymbolInfo | None,
    pin: SymbolPin,
    *,
    symbol_rotation: int = 0,
) -> Literal["left", "right", "top", "bottom"]:
    if symbol_info is not None and pin.at is not None:
        pin_x, pin_y = symbol_pin_coordinate(0.0, 0.0, pin, symbol_rotation=symbol_rotation)
        body_rects = _symbol_body_boxes(
            symbol_info,
            0.0,
            0.0,
            margin=0.0,
            symbol_rotation=symbol_rotation,
        )
        if body_rects:
            rotation = (int(pin.at[2]) + symbol_rotation) % 360
            return _nearest_pin_body_side(pin_x, pin_y, rotation, body_rects)
    if pin.at is not None and len(pin.at) > 2:
        rotation = (int(pin.at[2]) + symbol_rotation) % 360
        if rotation == 0:
            return "left"
        if rotation == 180:
            return "right"
        if rotation == 90:
            return "bottom"
        return "top"
    if symbol_info is not None:
        body = symbol_body_rect(symbol_info, 0.0, 0.0, margin=0.0)
        pin_x, pin_y = symbol_pin_coordinate(0.0, 0.0, pin, symbol_rotation=symbol_rotation)
        rect = _as_rect(body)
        center_x = (rect.left + rect.right) / 2
        center_y = (rect.top + rect.bottom) / 2
        if pin_x <= rect.left or pin_x < center_x - max(rect.width * 0.2, 1.27):
            return "left"
        if pin_x >= rect.right or pin_x > center_x + max(rect.width * 0.2, 1.27):
            return "right"
        if pin_y <= rect.top or pin_y < center_y:
            return "top"
        return "bottom"
    return "left"


def _nearest_pin_body_side(
    pin_x: float,
    pin_y: float,
    rotation: int,
    body_rects: tuple[Rect, ...],
) -> Literal["left", "right", "top", "bottom"]:
    if rotation in {0, 180}:
        vertical_edges = _pin_body_edge_candidates(pin_x, pin_y, body_rects, horizontal=False)
        if vertical_edges:
            return min(vertical_edges, key=lambda item: item[0])[1]
    if rotation in {90, 270}:
        horizontal_edges = _pin_body_edge_candidates(pin_x, pin_y, body_rects, horizontal=True)
        if horizontal_edges:
            return min(horizontal_edges, key=lambda item: item[0])[1]
    all_edges = [
        candidate
        for horizontal in (False, True)
        for candidate in _pin_body_edge_candidates(pin_x, pin_y, body_rects, horizontal=horizontal)
    ]
    return min(all_edges, key=lambda item: item[0])[1] if all_edges else "left"


def _pin_body_edge_candidates(
    pin_x: float,
    pin_y: float,
    body_rects: tuple[Rect, ...],
    *,
    horizontal: bool,
) -> list[tuple[float, Literal["left", "right", "top", "bottom"]]]:
    in_band: list[tuple[float, Literal["left", "right", "top", "bottom"]]] = []
    fallback: list[tuple[float, Literal["left", "right", "top", "bottom"]]] = []
    for rect in body_rects:
        if horizontal:
            candidates: tuple[tuple[float, Literal["left", "right", "top", "bottom"]], ...] = (
                (abs(pin_y - rect.top), "top"),
                (abs(pin_y - rect.bottom), "bottom"),
            )
            target = in_band if rect.left - 0.001 <= pin_x <= rect.right + 0.001 else fallback
        else:
            candidates = (
                (abs(pin_x - rect.left), "left"),
                (abs(pin_x - rect.right), "right"),
            )
            target = in_band if rect.top - 0.001 <= pin_y <= rect.bottom + 0.001 else fallback
        target.extend(candidates)
    return in_band or fallback


def _symbol_body_box(
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
    *,
    margin: float,
    symbol_rotation: int = 0,
) -> Rect:
    body = _as_rect(symbol_body_rect(symbol_info, x, y, margin=margin))
    return _rotate_rect_around_symbol(body, x, y, symbol_rotation)


def _symbol_body_boxes(
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
    *,
    margin: float,
    symbol_rotation: int = 0,
) -> tuple[Rect, ...]:
    local_rects = _symbol_graphic_rects(symbol_info)
    if not local_rects:
        return (_symbol_body_box(symbol_info, x, y, margin=margin, symbol_rotation=symbol_rotation),)
    return tuple(
        _rotate_rect_around_symbol(
            Rect(
                left=x + local_rect.left - margin,
                top=y - local_rect.bottom - margin,
                right=x + local_rect.right + margin,
                bottom=y - local_rect.top + margin,
            ),
            x,
            y,
            symbol_rotation,
        )
        for local_rect in local_rects
    )


def _symbol_graphic_rects(symbol_info: SymbolInfo | None) -> tuple[Rect, ...]:
    if symbol_info is None or symbol_info.definition is None:
        return ()
    rects: list[Rect] = []
    _collect_symbol_graphic_rects(symbol_info.definition, rects)
    return tuple(rects)


def _collect_symbol_graphic_rects(expr: object, rects: list[Rect]) -> None:
    if not isinstance(expr, list) or not expr:
        return
    if atom(expr[0]) == "rectangle":
        start = sexpr_point(expr, "start")
        end = sexpr_point(expr, "end")
        if start is not None and end is not None:
            rects.append(
                Rect(
                    left=min(start[0], end[0]),
                    top=min(start[1], end[1]),
                    right=max(start[0], end[0]),
                    bottom=max(start[1], end[1]),
                )
            )
        return
    for item in expr[1:]:
        _collect_symbol_graphic_rects(item, rects)


def _rotate_rect_around_symbol(rect: Rect, symbol_x: float, symbol_y: float, rotation: int) -> Rect:
    rotation = rotation % 360
    if rotation == 0:
        return rect
    points = [
        _rotate_absolute_point(rect.left, rect.top, symbol_x, symbol_y, rotation),
        _rotate_absolute_point(rect.left, rect.bottom, symbol_x, symbol_y, rotation),
        _rotate_absolute_point(rect.right, rect.top, symbol_x, symbol_y, rotation),
        _rotate_absolute_point(rect.right, rect.bottom, symbol_x, symbol_y, rotation),
    ]
    return Rect(
        left=min(point.x for point in points),
        top=min(point.y for point in points),
        right=max(point.x for point in points),
        bottom=max(point.y for point in points),
    )


def _rotate_absolute_point(
    point_x: float,
    point_y: float,
    symbol_x: float,
    symbol_y: float,
    rotation: int,
) -> Point:
    local_x = point_x - symbol_x
    local_y = symbol_y - point_y
    rotation = rotation % 360
    if rotation == 90:
        local_x, local_y = -local_y, local_x
    elif rotation == 180:
        local_x, local_y = -local_x, -local_y
    elif rotation == 270:
        local_x, local_y = local_y, -local_x
    return Point(symbol_x + local_x, symbol_y - local_y)


def segment_hits_rect_beyond_anchor(segment: WireSegment, rect: Rect) -> bool:
    if not segment_intersects_rect(segment, rect):
        return False
    start = (segment[0], segment[1])
    end = (segment[2], segment[3])
    start_inside = (
        rect.left - GEOMETRY_EPSILON <= start[0] <= rect.right + GEOMETRY_EPSILON
        and rect.top - GEOMETRY_EPSILON <= start[1] <= rect.bottom + GEOMETRY_EPSILON
    )
    end_inside = (
        rect.left - GEOMETRY_EPSILON <= end[0] <= rect.right + GEOMETRY_EPSILON
        and rect.top - GEOMETRY_EPSILON <= end[1] <= rect.bottom + GEOMETRY_EPSILON
    )
    if start_inside and not end_inside:
        midpoint = ((segment[0] + segment[2]) / 2, (segment[1] + segment[3]) / 2)
        return (
            rect.left + GEOMETRY_EPSILON < midpoint[0] < rect.right - GEOMETRY_EPSILON
            and rect.top + GEOMETRY_EPSILON < midpoint[1] < rect.bottom - GEOMETRY_EPSILON
        )
    if end_inside and not start_inside:
        midpoint = ((segment[0] + segment[2]) / 2, (segment[1] + segment[3]) / 2)
        return (
            rect.left + GEOMETRY_EPSILON < midpoint[0] < rect.right - GEOMETRY_EPSILON
            and rect.top + GEOMETRY_EPSILON < midpoint[1] < rect.bottom - GEOMETRY_EPSILON
        )
    return True


def _segment_hits_rect_beyond_anchor(segment: WireSegment, rect: Rect) -> bool:
    return segment_hits_rect_beyond_anchor(segment, rect)


def _as_rect(rect: TupleRect | Rect) -> Rect:
    if isinstance(rect, Rect):
        return rect
    return Rect(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])


def _owner_from_nets(nets: frozenset[str], *, default_owner: str) -> str:
    if len(nets) == 1:
        return next(iter(nets))
    return default_owner


def _snap_grid(value: float) -> float:
    return snap_grid(value, SCHEMATIC_GRID)

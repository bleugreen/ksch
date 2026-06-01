from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from math import floor
from pathlib import Path
import re
from typing import Any, Literal

from ksch.geometry import symbol_pin_coordinate
from ksch.ids import stable_uuid
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.layout import Point, Rect, title_block_rect_for_paper, usable_page_rect_for_paper, snap_grid
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.model.source import PinDirection, SymbolDecl
from ksch.placed import (
    PlacedHierarchicalLabel,
    PlacedItem,
    PlacedJunction,
    PlacedLabel,
    PlacedNoConnect,
    PlacedProperty,
    PlacedSheetBlock,
    PlacedSheetPin,
    PlacedSymbol,
    PlacedSymbolPin,
    PlacedText,
    PlacedWire,
)
from ksch.power_flags import (
    POWER_DRIVER_LIB_ID,
    POWER_PORT_LIB_ID,
    power_driver_symbol,
    power_driver_symbol_definition,
    power_flag_symbol,
    power_flag_symbol_definition,
    power_port_symbol,
    power_port_symbol_definition,
)
from ksch.resolver import ResolvedEndpoint, ResolvedProject
from ksch.schematic_geometry import (
    LayoutElement,
    LayoutSegment,
    compact_symbol_property_points,
    placed_items_geometry,
    segment_blocked_by_element,
    symbol_pin_side,
    symbol_property_points,
    text_rect,
)
from ksch.segment_geometry import segments_touch


PAPER = "A3"
GRID = 2.54
PIN_GRID = 1.27
PAGE_COMFORT_MARGIN = 17.78
ROOT_CLEARANCE = 7.62
SUPPORT_GAP = 6.35
SUPPORT_STEP = 7.62
BANK_COLUMN_STEP = 30.48
CAP_BANK_MAX_WIDTH = 110.0
CAP_BANK_ROW_GAP = 10.16
MARKER_BANK_ROW_STEP = 12.7
MARKER_BANK_COLUMN_STEP = 55.88
PACK_GAP = 1.27
RAIL_STACK_STEP = 17.78
ISLAND_GAP = 10.16
DIRECT_ISLAND_WIRE_LIMIT = 76.2
DIRECT_LOCAL_WIRE_LIMIT = 30.48
LABEL_GAP = 2.54
ROUTE_CANDIDATE_LIMIT = 64
ROUTE_BLOCKER_WEIGHT = 1_000_000.0
ROUTE_CONTACT_WEIGHT = 750_000.0
PORT_ESCAPE_WEIGHT = 5_000_000.0
TEXT_WIDTH = 1.27
SHEET_MIN_WIDTH = 76.2
SHEET_MIN_HEIGHT = 35.56
SHEET_PIN_STEP = 5.08
SHEET_PIN_OFFSET = 7.62

PortSide = Literal["NORTH", "EAST", "SOUTH", "WEST"]


@dataclass(frozen=True)
class SheetLayoutState:
    path: str
    filename: Path
    paper: str
    lib_symbols: tuple[list[Any], ...]
    items: tuple[PlacedItem, ...]
    instance_path: str
    page: str
    layout_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Port:
    endpoint_key: str
    terminal: str
    pin: SymbolPin | None
    local: Point
    side: PortSide


@dataclass(frozen=True)
class Component:
    id: str
    kind: Literal["symbol", "sheet", "power"]
    ports: dict[str, Port]
    passive: bool
    ref: str | None = None
    unit: int | None = None
    symbol_decl: SymbolDecl | None = None
    symbol_info: SymbolInfo | None = None
    sheet_block: PlacedSheetBlock | None = None
    power_net: str | None = None
    power_index: int | None = None


@dataclass(frozen=True)
class NetEndpoint:
    net_name: str
    component_id: str
    endpoint_key: str
    terminal: str
    endpoint: ResolvedEndpoint | None = None


@dataclass(frozen=True)
class PlacedComponent:
    component: Component
    at: Point
    rotation: int
    items: tuple[PlacedItem, ...]
    ports: dict[str, tuple[float, float]]
    port_sides: dict[str, PortSide]
    rect: Rect


@dataclass(frozen=True)
class Assembly:
    id: str
    items: tuple[PlacedItem, ...]
    rect: Rect
    ports: dict[str, tuple[float, float]]
    port_sides: dict[str, PortSide]
    component_ids: frozenset[str]


@dataclass(frozen=True)
class _WireRequest:
    net_name: str
    start: tuple[float, float]
    end: tuple[float, float]
    start_terminal: str | None
    end_terminal: str | None
    key: str
    start_side: PortSide | None = None
    end_side: PortSide | None = None


@dataclass(frozen=True)
class _RouteCandidate:
    request_index: int
    points: tuple[tuple[float, float], ...]
    score: float
    segments: tuple[tuple[frozenset[str], tuple[float, float, float, float]], ...]


@dataclass(frozen=True)
class _PassiveBankCandidate:
    placed: PlacedComponent
    wire_request: _WireRequest | None
    occupied: tuple[Rect, ...]
    score: float


@dataclass(frozen=True)
class _PassiveBankState:
    score: float
    items: tuple[PlacedItem, ...]
    occupied: tuple[Rect, ...]
    placed: tuple[PlacedComponent, ...]
    wire_requests: tuple[_WireRequest, ...]
    connected: frozenset[str]
    component_ids: frozenset[str]


@dataclass(frozen=True)
class _LabelRequest:
    net_name: str
    label_text: str
    point: tuple[float, float]
    side: PortSide
    kind: Literal["local", "hierarchical"]
    terminal: str | None
    endpoint_key: str
    axis_locked: bool


@dataclass(frozen=True)
class _LabelAnchorCandidate:
    anchor: tuple[float, float]
    justify: Literal["left", "right"]
    rect: Rect
    side: PortSide
    score: float
    routed_score: float


@dataclass(frozen=True)
class _RailCap:
    component_id: str
    rail_record: NetEndpoint
    ground_record: NetEndpoint


@dataclass(frozen=True)
class _OscillatorCap:
    component_id: str
    net_name: str
    signal_record: NetEndpoint
    ground_record: NetEndpoint


@dataclass(frozen=True)
class _OscillatorModule:
    bridge_id: str
    side: PortSide
    links: tuple[tuple[str, NetEndpoint, NetEndpoint], ...]
    caps: tuple[_OscillatorCap, ...]
    ground_records: tuple[NetEndpoint, ...]


@dataclass(frozen=True)
class _PackScoreContext:
    visible_index: "_RectIndex"
    route_index: "_RectIndex"
    envelope_index: "_RectIndex"
    placed_bounds: Rect | None


@dataclass(frozen=True)
class _PathContext:
    avoid_index: "_LayoutElementIndex"
    existing_index: "_SegmentIndex"


@dataclass(frozen=True)
class _PathChoice:
    points: tuple[tuple[float, float], ...]
    score: float
    blockers: int
    contacts: int


def solve_sheet_layout(project: ResolvedProject, sheet_path: str) -> SheetLayoutState:
    return _AssemblySolver(project, sheet_path).solve()


def sheet_filename(project_name: str, sheet_path: str) -> Path:
    if sheet_path == "/":
        return Path(f"{project_name}.kicad_sch")
    return Path("sheets").joinpath(*(part for part in sheet_path.split("/") if part)).with_suffix(".kicad_sch")


def sheet_instance_path(sheet_path: str) -> str:
    if sheet_path == "/":
        return "/"
    parent_path = "/"
    parts: list[str] = []
    for child_name in [part for part in sheet_path.split("/") if part]:
        parts.append(child_sheet_uuid(parent_path, child_name))
        parent_path = join_sheet_path(parent_path, child_name)
    return "/" + "/".join(parts)


def join_sheet_path(parent_path: str, child_name: str) -> str:
    return f"/{child_name}" if parent_path == "/" else f"{parent_path}/{child_name}"


def child_sheet_uuid(parent_path: str, child_name: str) -> str:
    return stable_uuid(f"{parent_path}/{child_name}:sheet")


def page_number(project: ResolvedProject, sheet_path: str) -> str:
    return str(sorted(project.source.sheets).index(sheet_path) + 1)


class _AssemblySolver:
    def __init__(self, project: ResolvedProject, sheet_path: str) -> None:
        self.project = project
        self.sheet_path = sheet_path
        self.sheet = project.source.sheets[sheet_path]
        self.resolved_sheet = project.sheets.get(sheet_path)
        self.components: dict[str, Component] = {}
        self.endpoint_to_component: dict[str, str] = {}
        self.net_records: dict[str, list[NetEndpoint]] = {}
        self.layout_errors: list[str] = []
        self._local_label_text_cache: dict[str, str] | None = None
        self._project_label_text_counts_cache: dict[str, int] | None = None
        self._implicit_driver_nets: set[str] = set()
        self._placed_component_cache: dict[tuple[str, int, bool], PlacedComponent] = {}

    def solve(self) -> SheetLayoutState:
        self._build_components()
        self._build_net_records()
        owners = self._passive_owners()
        assemblies = self._build_assemblies(owners)
        items, ports = self._pack_assemblies(assemblies)
        del ports
        items = self._legalized_sheet_items(items)
        items = self._ensure_power_driver_symbols(items)
        return SheetLayoutState(
            path=self.sheet_path,
            filename=sheet_filename(self.project.name, self.sheet_path),
            paper=PAPER,
            lib_symbols=self._lib_symbols(),
            items=tuple(items),
            instance_path=sheet_instance_path(self.sheet_path),
            page=page_number(self.project, self.sheet_path),
            layout_errors=tuple(self.layout_errors),
        )

    def _legalized_sheet_items(self, items: list[PlacedItem]) -> list[PlacedItem]:
        legalized = _drop_overlapping_junctions(items, self.project.symbol_library)
        for _iteration in range(3):
            next_items = self._demote_blocked_wire_segments(legalized)
            if len(next_items) == len(legalized):
                return next_items
            legalized = next_items
        return legalized

    def _ensure_power_driver_symbols(self, items: list[PlacedItem]) -> list[PlacedItem]:
        normalized: list[PlacedItem] = []
        for item in items:
            if isinstance(item, PlacedLabel) and len(item.nets) == 1:
                net_name = next(iter(item.nets))
                if _is_power_net(net_name) and item.name != net_name:
                    value_at = (_snap(item.at[0] + LABEL_GAP), item.at[1])
                    key = f"final-power-label:{item.uuid}"
                    normalized.append(
                        power_port_symbol(
                            self.sheet_path,
                            net_name,
                            key,
                            Point(item.at[0], item.at[1]),
                            Point(value_at[0], value_at[1]),
                            value=net_name,
                            justify="left",
                            rotation=(-_power_port_symbol_rotation("EAST")) % 360,
                            symbol_rotation=_power_port_symbol_rotation("EAST"),
                            hidden_value=True,
                            project_name=self.project.name,
                            sheet_instance_path=sheet_instance_path(self.sheet_path),
                        )
                    )
                    normalized.append(
                        PlacedText(
                            text=item.name,
                            at=value_at,
                            uuid=stable_uuid(f"{self.sheet_path}:{key}:text"),
                            justify="left",
                        )
                    )
                    continue
            normalized.append(item)
        items = normalized
        power_ports: list[tuple[str, PlacedSymbol]] = []
        for item in items:
            if not isinstance(item, PlacedSymbol) or item.lib_id != POWER_PORT_LIB_ID:
                continue
            net_name = _symbol_property_value(item, "Value")
            if net_name is not None:
                power_ports.append((net_name, item))

        driven_points = {
            item.at
            for item in items
            if isinstance(item, PlacedSymbol) and item.lib_id == POWER_DRIVER_LIB_ID
        }
        driven_nets = {
            net_name
            for net_name, port in power_ports
            if port.at in driven_points
        }
        added: list[PlacedItem] = []
        for net_name, port in power_ports:
            if net_name in driven_nets:
                continue
            if not _is_power_net(net_name):
                continue
            if self._project_net_has_driver(net_name):
                continue
            if self._first_sheet_for_net(net_name) != self.sheet_path:
                continue
            added.append(
                power_driver_symbol(
                    self.sheet_path,
                    net_name,
                    f"final-driver:{port.uuid}",
                    Point(port.at[0], port.at[1]),
                    project_name=self.project.name,
                    sheet_instance_path=sheet_instance_path(self.sheet_path),
                )
            )
            driven_nets.add(net_name)
        return [*items, *added]

    def _demote_blocked_wire_segments(self, items: list[PlacedItem]) -> list[PlacedItem]:
        geometry = placed_items_geometry(tuple(items), symbol_library=self.project.symbol_library)
        blockers = geometry.route_blockers()
        blocked_ids = {
            segment.id
            for segment, _blocker in blockers
            if segment.kind == "wire" and segment.nets
        }
        if not blocked_ids:
            return items
        remaining = [
            item
            for item in items
            if not (isinstance(item, PlacedWire) and item.uuid in blocked_ids)
        ]
        occupied = _occupied_rects(tuple(remaining), self.project.symbol_library, margin=GRID / 2)
        occupied.extend(_wire_avoid_rects(remaining))
        added: list[PlacedItem] = []
        labeled: set[tuple[str, tuple[float, float]]] = set()
        for segment, _blocker in blockers:
            if segment.id not in blocked_ids or not segment.nets:
                continue
            net_name = sorted(segment.nets)[0]
            kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
            start = (segment.start.x, segment.start.y)
            end = (segment.end.x, segment.end.y)
            for suffix, point, terminals, side in (
                ("start", start, segment.start_terminals, _segment_endpoint_side(start, end)),
                ("end", end, segment.end_terminals, _segment_endpoint_side(end, start)),
            ):
                label_key = (net_name, point)
                if label_key in labeled:
                    continue
                labeled.add(label_key)
                label_items = _label_items(
                    self.sheet_path,
                    net_name,
                    self._label_text(net_name, kind),
                    point,
                    side,
                    kind,
                    occupied,
                    sorted(terminals)[0] if terminals else None,
                    f"legalize:{segment.id}:{suffix}",
                    axis_locked=False,
                    existing_items=[*remaining, *added],
                    symbol_library=self.project.symbol_library,
                )
                added.extend(label_items)
                occupied.extend(_wire_avoid_rects(label_items))
        return [*remaining, *added]

    def _build_components(self) -> None:
        for ref, decl in sorted(self.sheet.symbols.items()):
            symbol_info = self.project.symbol_library.get(decl.lib)
            for unit in _symbol_units(decl.units, symbol_info):
                info = _unit_symbol_info(symbol_info, unit)
                component = self._symbol_component(ref, unit, decl, info)
                self.components[component.id] = component
                for endpoint_key in component.ports:
                    self.endpoint_to_component[endpoint_key] = component.id
        for child_name, child in sorted(self.sheet.child_instances.items()):
            component = self._sheet_component(child_name, child.target_path)
            self.components[component.id] = component
            for endpoint_key in component.ports:
                self.endpoint_to_component[endpoint_key] = component.id
        for index, net_name in enumerate(self.sheet.power_flags):
            component = self._power_component(index, net_name)
            self.components[component.id] = component
            for endpoint_key in component.ports:
                self.endpoint_to_component[endpoint_key] = component.id

    def _symbol_component(
        self,
        ref: str,
        unit: int,
        decl: SymbolDecl,
        symbol_info: SymbolInfo | None,
    ) -> Component:
        ports: dict[str, Port] = {}
        if symbol_info is not None:
            seen: set[str] = set()
            for pin in symbol_info.pins:
                if pin.number in seen:
                    continue
                seen.add(pin.number)
                point = symbol_pin_coordinate(0.0, 0.0, pin)
                endpoint_key = _symbol_endpoint_key(ref, unit, pin.number)
                ports[endpoint_key] = Port(
                    endpoint_key=endpoint_key,
                    terminal=f"{ref}.{pin.name}@{pin.number}",
                    pin=pin,
                    local=Point(point[0], point[1]),
                    side=_pin_side(symbol_info, pin),
                )
        return Component(
            id=_symbol_component_id(ref, unit),
            kind="symbol",
            ports=ports,
            passive=_is_passive(ref, symbol_info),
            ref=ref,
            unit=unit,
            symbol_decl=decl,
            symbol_info=symbol_info,
        )

    def _sheet_component(self, child_name: str, target_path: str) -> Component:
        child_sheet = self.project.source.sheets[target_path]
        left_ports, right_ports = _split_sheet_ports(sorted(child_sheet.interface.items()))
        pin_count = max(len(left_ports), len(right_ports))
        pin_step = _sheet_pin_step(pin_count)
        width = SHEET_MIN_WIDTH
        height = max(SHEET_MIN_HEIGHT, SHEET_PIN_OFFSET + pin_count * pin_step + GRID * 2)
        pins: list[PlacedSheetPin] = []
        ports: dict[str, Port] = {}
        for index, (name, direction) in enumerate(left_ports):
            y = _snap(SHEET_PIN_OFFSET + index * pin_step)
            uuid = stable_uuid(f"{self.sheet_path}:{child_name}:{name}:pin")
            pins.append(PlacedSheetPin(name, _sheet_pin_shape(direction), (0.0, y), 180, uuid))
            key = _sheet_endpoint_key(child_name, name)
            ports[key] = Port(key, f"{child_name}.{name}", None, Point(0.0, y), "WEST")
        for index, (name, direction) in enumerate(right_ports):
            y = _snap(SHEET_PIN_OFFSET + index * pin_step)
            uuid = stable_uuid(f"{self.sheet_path}:{child_name}:{name}:pin")
            pins.append(PlacedSheetPin(name, _sheet_pin_shape(direction), (width, y), 0, uuid))
            key = _sheet_endpoint_key(child_name, name)
            ports[key] = Port(key, f"{child_name}.{name}", None, Point(width, y), "EAST")
        block = PlacedSheetBlock(
            at=(0.0, 0.0),
            size=(width, height),
            uuid=child_sheet_uuid(self.sheet_path, child_name),
            sheet_name=child_name,
            sheet_file=sheet_filename(self.project.name, target_path).as_posix(),
            sheet_name_at=(1.27, -1.27),
            sheet_file_at=(1.27, height + 2.54),
            pins=tuple(pins),
            project_name=self.project.name,
            sheet_instance_path=sheet_instance_path(target_path),
            page=page_number(self.project, target_path),
        )
        return Component(
            id=_sheet_component_id(child_name),
            kind="sheet",
            ports=ports,
            passive=False,
            ref=child_name,
            sheet_block=block,
        )

    def _power_component(self, index: int, net_name: str) -> Component:
        key = _power_endpoint_key(index, net_name)
        return Component(
            id=f"power:{index}",
            kind="power",
            ports={key: Port(key, f"PWR_FLAG.{net_name}@{index}", None, Point(0.0, 0.0), "SOUTH")},
            passive=False,
            ref=f"PWR_FLAG:{net_name}",
            power_net=net_name,
            power_index=index,
        )

    def _build_net_records(self) -> None:
        if self.resolved_sheet is not None:
            for net_name, endpoints in sorted(self.resolved_sheet.nets.items()):
                for endpoint in endpoints:
                    record = self._net_endpoint(net_name, endpoint)
                    if record is not None:
                        self.net_records.setdefault(net_name, []).append(record)
        for index, net_name in enumerate(self.sheet.power_flags):
            key = _power_endpoint_key(index, net_name)
            component_id = self.endpoint_to_component.get(key)
            if component_id is None:
                continue
            port = self.components[component_id].ports[key]
            self.net_records.setdefault(net_name, []).append(NetEndpoint(net_name, component_id, key, port.terminal))
        self._local_label_text_cache = None

    def _label_text(self, net_name: str, kind: Literal["local", "hierarchical"]) -> str:
        if kind == "hierarchical":
            return net_name
        return self._local_label_texts().get(net_name, net_name)

    def _local_label_texts(self) -> dict[str, str]:
        if self._local_label_text_cache is not None:
            return self._local_label_text_cache
        prefix = self._sheet_qualified_prefix()
        if prefix is None:
            self._local_label_text_cache = {net_name: net_name for net_name in self.net_records}
            return self._local_label_text_cache
        raw: dict[str, str] = {}
        for net_name in self.net_records:
            if net_name in self.sheet.interface:
                raw[net_name] = net_name
                continue
            raw[net_name] = net_name[len(prefix):] if net_name.startswith(prefix) and len(net_name) > len(prefix) else net_name
        counts: dict[str, int] = {}
        for text in raw.values():
            counts[text] = counts.get(text, 0) + 1
        compressed: dict[str, str] = {}
        compressed_counts: dict[str, int] = {}
        for net_name, text in raw.items():
            _head, separator, tail = text.partition("_")
            candidate = tail if separator and tail else text
            compressed[net_name] = candidate
            compressed_counts[candidate] = compressed_counts.get(candidate, 0) + 1
        self._local_label_text_cache = {
            net_name: (
                compressed[net_name]
                if counts[text] == 1
                and compressed_counts[compressed[net_name]] == 1
                and counts.get(compressed[net_name], 0) == 0
                else text if counts[text] == 1 else net_name
            )
            for net_name, text in raw.items()
        }
        return self._local_label_text_cache

    def _sheet_qualified_prefix(self) -> str | None:
        return _sheet_net_prefix(self.sheet_path, self.net_records)

    def _power_label_text(self, net_name: str) -> str:
        return self._label_text(net_name, "local")

    def _project_label_text_counts(self) -> dict[str, int]:
        if self._project_label_text_counts_cache is not None:
            return self._project_label_text_counts_cache
        counts: dict[str, int] = {}
        for sheet_path, resolved_sheet in self.project.sheets.items():
            prefix = _sheet_net_prefix(sheet_path, resolved_sheet.nets)
            interface = self.project.source.sheets[sheet_path].interface
            for net_name in resolved_sheet.nets:
                if prefix is None or net_name in interface or not net_name.startswith(prefix):
                    text = net_name
                else:
                    text = net_name[len(prefix):]
                counts[text] = counts.get(text, 0) + 1
        self._project_label_text_counts_cache = counts
        return counts

    def _net_endpoint(self, net_name: str, endpoint: ResolvedEndpoint) -> NetEndpoint | None:
        endpoint_key = self._endpoint_key(endpoint)
        if endpoint_key is None:
            self.layout_errors.append(f"{self.sheet_path}: no placed endpoint for {endpoint.text}")
            return None
        component_id = self.endpoint_to_component.get(endpoint_key)
        if component_id is None:
            self.layout_errors.append(f"{self.sheet_path}: no component for {endpoint.text}")
            return None
        port = self.components[component_id].ports[endpoint_key]
        return NetEndpoint(net_name, component_id, endpoint_key, port.terminal, endpoint)

    def _endpoint_key(self, endpoint: ResolvedEndpoint) -> str | None:
        if endpoint.kind is EndpointKind.SHEET_PORT:
            if endpoint.child_sheet is None or endpoint.port is None:
                return None
            return _sheet_endpoint_key(endpoint.child_sheet, endpoint.port)
        if endpoint.ref is None:
            return None
        decl = self.sheet.symbols.get(endpoint.ref)
        info = self.project.symbol_library.get(decl.lib) if decl is not None else None
        pin = _resolved_endpoint_pin(info, endpoint)
        if pin is None:
            return None
        unit = pin.unit if pin.unit != 0 else 1
        return _symbol_endpoint_key(endpoint.ref, unit, pin.number)

    def _passive_owners(self) -> dict[str, str]:
        owners: dict[str, str] = {}
        shared_rail_cap_ids = self._shared_rail_cap_ids()
        for component_id, component in self.components.items():
            if not _is_local_support_component(component):
                continue
            if component_id in shared_rail_cap_ids:
                continue
            choice = self._direct_owner(component_id)
            if choice is not None:
                owners[component_id] = choice
        return owners

    def _direct_owner(self, passive_id: str) -> str | None:
        best: tuple[float, str] | None = None
        for net_name, records in self.net_records.items():
            if _is_ground_net(net_name):
                continue
            if not any(record.component_id == passive_id for record in records):
                continue
            component = self.components[passive_id]
            if _is_power_net(net_name) and len(component.ports) > 2:
                continue
            for record in records:
                peer = self.components[record.component_id]
                if peer.passive or peer.id == passive_id:
                    continue
                score = 100.0
                if _is_power_net(net_name):
                    score = 45.0
                score += min(40.0, len(peer.ports) * 0.4)
                option = (score, peer.id)
                if best is None or option > best:
                    best = option
        return best[1] if best is not None and best[0] > 10.0 else None

    def _expanded_support_owners(self, owners: dict[str, str]) -> dict[str, str]:
        expanded = dict(owners)
        for root_id in sorted(set(owners.values())):
            owned = {component_id for component_id, owner_id in expanded.items() if owner_id == root_id}
            changed = True
            while changed:
                changed = False
                local_ids = owned | {root_id}
                for net_name, records in sorted(self.net_records.items()):
                    if _is_ground_net(net_name) or _is_power_net(net_name):
                        continue
                    if not any(record.component_id in local_ids for record in records):
                        continue
                    if not self._net_local_to_support_root(net_name, root_id):
                        continue
                    for record in records:
                        component_id = record.component_id
                        if component_id == root_id or component_id in expanded:
                            continue
                        if not _is_local_support_component(self.components[component_id]):
                            continue
                        expanded[component_id] = root_id
                        owned.add(component_id)
                        changed = True
        return expanded

    def _net_local_to_support_root(self, net_name: str, root_id: str) -> bool:
        for record in self.net_records.get(net_name, []):
            component_id = record.component_id
            if component_id == root_id:
                continue
            if _is_local_support_component(self.components[component_id]):
                continue
            return False
        return True

    def _build_assemblies(self, owners: dict[str, str]) -> list[Assembly]:
        assemblies: list[Assembly] = []
        placed: set[str] = set()
        owners = self._expanded_support_owners(owners)
        root_owned = {
            root_id: sorted(component_id for component_id, owner_id in owners.items() if owner_id == root_id)
            for root_id in self.components
        }
        for island in self._active_islands():
            if len(island) == 1:
                root_id = island[0]
                assembly = self._root_assembly(root_id, root_owned.get(root_id, []))
                assemblies.append(assembly)
                placed.update(assembly.component_ids)
            else:
                assembly = self._island_assembly(island, root_owned)
                if _assembly_fits_content(assembly):
                    assemblies.append(assembly)
                    placed.update(assembly.component_ids)
                else:
                    for root_id in island:
                        root_assembly = self._root_assembly(root_id, root_owned.get(root_id, []))
                        assemblies.append(root_assembly)
                        placed.update(root_assembly.component_ids)
        for root_id, root in sorted(self.components.items()):
            if root.passive or root.kind == "power" or root_id in placed:
                continue
            assembly = self._root_assembly(root_id, root_owned.get(root_id, []))
            assemblies.append(assembly)
            placed.update(assembly.component_ids)
        for root_id, root in sorted(self.components.items()):
            if root.passive or root.kind != "power" or root_id in placed:
                continue
            assembly = self._root_assembly(root_id, root_owned.get(root_id, []))
            assemblies.append(assembly)
            placed.update(assembly.component_ids)
        for assembly in self._shared_rail_cap_bank_assemblies(placed):
            assemblies.append(assembly)
            placed.update(assembly.component_ids)
        for assembly in self._loose_marker_bank_assemblies(placed):
            assemblies.append(assembly)
            placed.update(assembly.component_ids)
        for assembly in self._standalone_symbol_bank_assemblies(placed):
            assemblies.append(assembly)
            placed.update(assembly.component_ids)
        for component_ids in self._unplaced_components(placed):
            assembly = self._floating_assembly(component_ids)
            assemblies.append(assembly)
            placed.update(assembly.component_ids)
        return assemblies

    def _active_islands(self) -> list[tuple[str, ...]]:
        active = {
            component_id
            for component_id, component in self.components.items()
            if _is_core_component(component)
        }
        graph = {component_id: set[str]() for component_id in active}
        for net_name, records in self.net_records.items():
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            ids = sorted({record.component_id for record in records if record.component_id in active})
            for first in ids:
                graph[first].update(second for second in ids if second != first)
        remaining = set(active)
        islands: list[tuple[str, ...]] = []
        while remaining:
            start = min(remaining)
            stack = [start]
            island: set[str] = set()
            while stack:
                current = stack.pop()
                if current in island:
                    continue
                island.add(current)
                stack.extend(sorted(graph.get(current, set()) - island))
            remaining -= island
            islands.append(tuple(sorted(island)))
        return sorted(islands, key=lambda island: (-len(island), island))

    def _unplaced_components(self, placed: set[str]) -> list[tuple[str, ...]]:
        remaining = {component_id for component_id in self.components if component_id not in placed}
        graph = {component_id: set[str]() for component_id in remaining}
        for net_name, records in self.net_records.items():
            if _is_ground_net(net_name):
                continue
            ids = [record.component_id for record in records if record.component_id in remaining]
            for first in ids:
                graph.setdefault(first, set()).update(second for second in ids if second != first)
        groups: list[tuple[str, ...]] = []
        while remaining:
            start = min(remaining)
            stack = [start]
            group: set[str] = set()
            while stack:
                current = stack.pop()
                if current in group:
                    continue
                group.add(current)
                stack.extend(sorted(graph.get(current, set()) - group))
            remaining -= group
            groups.append(tuple(sorted(group)))
        return groups

    def _root_assembly(
        self,
        root_id: str,
        owned_ids: list[str],
        island_peer_ids: frozenset[str] = frozenset(),
    ) -> Assembly:
        root = self.components[root_id]
        placed_root = self._place_component(root, Point(0.0, 0.0), 0)
        items: list[PlacedItem] = list(placed_root.items)
        occupied = _occupied_rects(placed_root.items, self.project.symbol_library, margin=GRID / 2)
        if root.kind == "symbol":
            occupied.extend(
                self._root_label_reservations(
                    root_id,
                    placed_root,
                    excluded_endpoints=self._owned_root_endpoint_keys(root_id, set(owned_ids)),
                )
            )
        placed_components = {root_id: placed_root}
        component_ids = {root_id}
        connected_endpoints: set[str] = set()
        wire_requests: list[_WireRequest] = []

        oscillator_items, oscillator_placed, oscillator_connected, oscillator_component_ids = self._oscillator_modules(
            root_id,
            owned_ids,
            placed_root,
            occupied,
        )
        items.extend(oscillator_items)
        placed_components.update(oscillator_placed)
        component_ids.update(oscillator_component_ids)
        connected_endpoints.update(oscillator_connected)
        remaining_owned_ids = [component_id for component_id in owned_ids if component_id not in component_ids]

        bridge_items, bridge_placed, bridge_connected, bridge_component_ids, bridge_wires = self._support_bridge_modules(
            root_id,
            remaining_owned_ids,
            placed_root,
            occupied,
        )
        items.extend(bridge_items)
        placed_components.update(bridge_placed)
        component_ids.update(bridge_component_ids)
        connected_endpoints.update(bridge_connected)
        wire_requests.extend(bridge_wires)
        side_lanes = self._side_lanes(root_id, remaining_owned_ids, placed_root)
        side_lanes = {
            side: [lane for lane in lanes if lane[0] not in bridge_component_ids]
            for side, lanes in side_lanes.items()
        }
        bridge_adjacent_ids = {
            component_id
            for component_id in remaining_owned_ids
            if component_id not in bridge_component_ids
            and self._shares_signal_with_any(component_id, bridge_component_ids)
        }
        side_lanes = {
            side: [lane for lane in lanes if lane[0] not in bridge_adjacent_ids]
            for side, lanes in side_lanes.items()
        }
        rail_items, rail_placed, rail_connected, rail_component_ids = self._support_rail_modules(
            root_id,
            side_lanes,
            placed_root,
            occupied,
        )
        items.extend(rail_items)
        placed_components.update(rail_placed)
        component_ids.update(rail_component_ids)
        connected_endpoints.update(rail_connected)
        side_lanes = {
            side: [lane for lane in lanes if lane[0] not in rail_component_ids]
            for side, lanes in side_lanes.items()
        }
        for side in ("WEST", "EAST", "NORTH", "SOUTH"):
            bank = self._place_side_passive_bank(
                side,
                side_lanes[side],
                placed_components,
                occupied,
                items,
            )
            items.extend(bank.items)
            occupied.extend(bank.occupied)
            wire_requests.extend(bank.wire_requests)
            connected_endpoints.update(bank.connected)
            component_ids.update(bank.component_ids)
            for placed in bank.placed:
                placed_components[placed.component.id] = placed
        progress = True
        while progress:
            progress = False
            for component_id in sorted(set(owned_ids) - component_ids):
                placement = self._place_indirect_support_component(
                    component_id,
                    placed_components,
                    occupied,
                    outward_peer_ids=bridge_component_ids,
                )
                if placement is None:
                    continue
                placed, net_name, peer_record, support_record = placement
                placed_components[component_id] = placed
                component_ids.add(component_id)
                items.extend(placed.items)
                occupied.extend(_occupied_rects(placed.items, self.project.symbol_library, margin=GRID / 2))
                peer_point = placed_components[peer_record.component_id].ports[peer_record.endpoint_key]
                support_point = placed.ports[support_record.endpoint_key]
                if not _is_power_net(net_name) and net_name not in self.sheet.interface:
                    wire_requests.append(
                        _WireRequest(
                            net_name,
                            peer_point,
                            support_point,
                            peer_record.terminal,
                            support_record.terminal,
                            f"assembly:{root_id}:{component_id}:{net_name}",
                            placed_components[peer_record.component_id].port_sides[peer_record.endpoint_key],
                            placed.port_sides[support_record.endpoint_key],
                        )
                    )
                    connected_endpoints.update({peer_record.endpoint_key, support_record.endpoint_key})
                progress = True
        for component_id in sorted(set(owned_ids) - component_ids):
            component = self.components[component_id]
            placed = self._place_component(component, Point(placed_root.rect.right + SUPPORT_STEP, placed_root.rect.top), 0, compact_value=True)
            placed_components[component_id] = placed
            component_ids.add(component_id)
            items.extend(placed.items)
            occupied.extend(_occupied_rects(placed.items, self.project.symbol_library, margin=GRID / 2))

        connected_endpoints.update(self._island_internal_root_endpoints(root_id, island_peer_ids))
        wire_items = self._route_or_label_wire_requests(wire_requests, items, occupied)
        items.extend(wire_items)
        net_items = self._assembly_net_items(component_ids, placed_components, occupied, connected_endpoints, base_items_extra=items)
        items.extend(net_items)
        occupied.extend(_wire_avoid_rects(net_items))
        cap_bank_items, cap_bank_connected, cap_bank_component_ids = self._root_rail_cap_bank_modules(
            root_id,
            placed_root,
            occupied,
        )
        items.extend(cap_bank_items)
        component_ids.update(cap_bank_component_ids)
        connected_endpoints.update(cap_bank_connected)
        rect = _items_rect(tuple(items), self.project.symbol_library) or Rect(0.0, 0.0, 0.0, 0.0)
        return _normalize_assembly(
            Assembly(
                root_id,
                tuple(items),
                rect,
                _ports_for(placed_components),
                _port_sides_for(placed_components),
                frozenset(component_ids),
            )
        )

    def _oscillator_modules(
        self,
        root_id: str,
        owned_ids: list[str],
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> tuple[list[PlacedItem], dict[str, PlacedComponent], set[str], set[str]]:
        items: list[PlacedItem] = []
        placed: dict[str, PlacedComponent] = {}
        connected: set[str] = set()
        component_ids: set[str] = set()
        base_items = list(placed_root.items)
        owned_set = set(owned_ids)
        for module in self._oscillator_module_candidates(root_id, owned_set, placed_root):
            if module.bridge_id in component_ids or any(cap.component_id in component_ids for cap in module.caps):
                continue
            built = self._place_oscillator_module(module, placed_root, occupied, [*base_items, *items])
            if built is None:
                continue
            module_items, module_placed, module_connected, module_component_ids = built
            items.extend(module_items)
            placed.update(module_placed)
            connected.update(module_connected)
            component_ids.update(module_component_ids)
            occupied.extend(_occupied_rects(tuple(module_items), self.project.symbol_library, margin=GRID / 2))
            occupied.extend(_wire_avoid_rects(module_items))
        return items, placed, connected, component_ids

    def _oscillator_module_candidates(
        self,
        root_id: str,
        owned_ids: set[str],
        placed_root: PlacedComponent,
    ) -> list[_OscillatorModule]:
        modules: list[_OscillatorModule] = []
        for bridge_id in sorted(owned_ids):
            bridge = self.components[bridge_id]
            if not _is_oscillator_bridge_component(bridge):
                continue
            links: list[tuple[str, NetEndpoint, NetEndpoint]] = []
            for net_name, records in sorted(self.net_records.items()):
                if _is_ground_net(net_name) or _is_power_net(net_name):
                    continue
                root_records = [record for record in records if record.component_id == root_id]
                bridge_records = [record for record in records if record.component_id == bridge_id]
                if root_records and bridge_records:
                    links.append((net_name, root_records[0], bridge_records[0]))
            if len(links) != 2:
                continue
            root_sides = [placed_root.port_sides[root_record.endpoint_key] for _net_name, root_record, _bridge_record in links]
            side = root_sides[0]
            if side not in {"WEST", "EAST"} or any(candidate != side for candidate in root_sides):
                continue
            caps: list[_OscillatorCap] = []
            used_caps: set[str] = set()
            for net_name, _root_record, _bridge_record in links:
                cap = self._oscillator_cap_for_net(net_name, owned_ids - {bridge_id} - used_caps)
                if cap is None:
                    break
                caps.append(cap)
                used_caps.add(cap.component_id)
            if len(caps) != len(links):
                continue
            ground_records = [
                record
                for records in self.net_records.values()
                for record in records
                if _is_ground_net(record.net_name)
                and (record.component_id == bridge_id or record.component_id in used_caps)
            ]
            if len(ground_records) < len(caps):
                continue
            modules.append(
                _OscillatorModule(
                    bridge_id=bridge_id,
                    side=side,
                    links=tuple(
                        sorted(
                            links,
                            key=lambda link: (
                                placed_root.ports[link[1].endpoint_key][1],
                                link[0],
                            ),
                        )
                    ),
                    caps=tuple(caps),
                    ground_records=tuple(sorted(ground_records, key=lambda record: record.endpoint_key)),
                )
            )
        return modules

    def _oscillator_cap_for_net(
        self,
        net_name: str,
        candidate_ids: set[str],
    ) -> _OscillatorCap | None:
        for component_id in sorted(candidate_ids, key=_component_ref_sort_key):
            component = self.components[component_id]
            if not _is_capacitor_component(component) or len(component.ports) != 2:
                continue
            signal_records = [
                record
                for record in self.net_records.get(net_name, [])
                if record.component_id == component_id
            ]
            ground_records = [
                record
                for records in self.net_records.values()
                for record in records
                if record.component_id == component_id and _is_ground_net(record.net_name)
            ]
            if len(signal_records) == 1 and len(ground_records) == 1:
                return _OscillatorCap(component_id, net_name, signal_records[0], ground_records[0])
        return None

    def _place_oscillator_module(
        self,
        module: _OscillatorModule,
        placed_root: PlacedComponent,
        occupied: list[Rect],
        existing_items: list[PlacedItem],
    ) -> tuple[list[PlacedItem], dict[str, PlacedComponent], set[str], set[str]] | None:
        placed_bridge = self._place_oscillator_bridge(module, placed_root, occupied)
        if placed_bridge is None:
            return None

        items: list[PlacedItem] = list(placed_bridge.items)
        placed = {module.bridge_id: placed_bridge}
        connected: set[str] = set()
        component_ids = {module.bridge_id}
        bridge_occupied = [*occupied, *_occupied_rects(placed_bridge.items, self.project.symbol_library, margin=GRID / 2)]

        cap_records = {cap.net_name: cap for cap in module.caps}
        bridge_signal_points = [
            placed_bridge.ports[bridge_record.endpoint_key]
            for _net_name, _root_record, bridge_record in module.links
        ]
        center_x = _snap(sum(point[0] for point in bridge_signal_points) / len(bridge_signal_points))
        cap_signal_y = _snap(placed_bridge.rect.bottom + SUPPORT_GAP)
        cap_pitch = SUPPORT_STEP * 2
        cap_offsets = [(_index - (len(module.caps) - 1) / 2) * cap_pitch for _index in range(len(module.caps))]
        cap_layout_links = sorted(
            module.links,
            key=lambda link: (
                placed_bridge.ports[link[2].endpoint_key][0]
                if module.side in {"WEST", "EAST"}
                else placed_bridge.ports[link[2].endpoint_key][1],
                link[0],
            ),
        )
        for index, (net_name, _root_record, bridge_record) in enumerate(cap_layout_links):
            cap = cap_records.get(net_name)
            if cap is None:
                return None
            cap_component = self.components[cap.component_id]
            signal_port = cap_component.ports[cap.signal_record.endpoint_key]
            rotation = _rotation_between_sides(signal_port.side, "NORTH")
            target = (_snap(center_x + cap_offsets[index]), cap_signal_y)
            at = _component_at_for_port(cap_component, signal_port, target, rotation)
            placed_cap = self._place_component(cap_component, Point(at[0], at[1]), rotation, compact_value=True)
            placed[cap.component_id] = placed_cap
            component_ids.add(cap.component_id)
            items.extend(placed_cap.items)
            bridge_occupied.extend(_occupied_rects(placed_cap.items, self.project.symbol_library, margin=GRID / 2))

        wire_requests: list[_WireRequest] = []
        for net_name, root_record, bridge_record in module.links:
            root_point = placed_root.ports[root_record.endpoint_key]
            bridge_point = placed_bridge.ports[bridge_record.endpoint_key]
            cap = cap_records[net_name]
            cap_point = placed[cap.component_id].ports[cap.signal_record.endpoint_key]
            wire_requests.append(
                _WireRequest(
                    net_name,
                    root_point,
                    bridge_point,
                    root_record.terminal,
                    bridge_record.terminal,
                    f"oscillator:{module.bridge_id}:{net_name}:root-bridge",
                    placed_root.port_sides[root_record.endpoint_key],
                    placed_bridge.port_sides[bridge_record.endpoint_key],
                )
            )
            wire_requests.append(
                _WireRequest(
                    net_name,
                    bridge_point,
                    cap_point,
                    bridge_record.terminal,
                    cap.signal_record.terminal,
                    f"oscillator:{module.bridge_id}:{cap.component_id}:{net_name}:load-cap",
                    placed_bridge.port_sides[bridge_record.endpoint_key],
                    placed[cap.component_id].port_sides[cap.signal_record.endpoint_key],
                )
            )
            connected.update({root_record.endpoint_key, bridge_record.endpoint_key, cap.signal_record.endpoint_key})

        cap_ground_pins = [
            (cap, placed[cap.component_id].ports[cap.ground_record.endpoint_key])
            for cap in module.caps
        ]
        gnd_text = self._power_label_text("GND")
        ground_y = _snap(max(point[1] for _cap, point in cap_ground_pins) + GRID * 2)
        cap_ground_anchors = [
            (cap, (_snap(point[0]), ground_y))
            for cap, point in cap_ground_pins
        ]
        bridge_ground_records = [
            record for record in module.ground_records if record.component_id == module.bridge_id
        ]
        bridge_ground_anchors: list[tuple[NetEndpoint, tuple[float, float]]] = []
        bridge_top_ground_records: list[NetEndpoint] = []
        for record in bridge_ground_records:
            point = placed_bridge.ports[record.endpoint_key]
            side = placed_bridge.port_sides[record.endpoint_key]
            if side == "NORTH":
                bridge_top_ground_records.append(record)
            else:
                anchor = (_snap(point[0]), ground_y)
                bridge_ground_anchors.append((record, anchor))
            connected.add(record.endpoint_key)

        rail_points = [
            *(anchor for _cap, anchor in cap_ground_anchors),
            *(anchor for _record, anchor in bridge_ground_anchors),
        ]
        rail_left = _snap(min(point[0] for point in rail_points) - GRID)
        rail_right = _snap(max(point[0] for point in rail_points) + GRID)
        rail_items = _rail_wire_items(
            self.sheet_path,
            "GND",
            [(rail_left, ground_y), *rail_points, (rail_right, ground_y)],
            set(),
            f"oscillator:{module.bridge_id}:gnd-rail",
        )
        items.extend(rail_items)
        for cap, pin_point in cap_ground_pins:
            anchor = dict(cap_ground_anchors)[cap]
            items.extend(
                _wire_items_from_points(
                    self.sheet_path,
                    "GND",
                    [pin_point, anchor],
                    cap.ground_record.terminal,
                    None,
                    f"oscillator:{module.bridge_id}:{cap.component_id}:gnd-stub",
                )
            )
            connected.add(cap.ground_record.endpoint_key)
        for record, anchor in bridge_ground_anchors:
            point = placed_bridge.ports[record.endpoint_key]
            side = placed_bridge.port_sides[record.endpoint_key]
            items.extend(
                _wire_items_avoiding(
                    self.sheet_path,
                    "GND",
                    point,
                    anchor,
                    record.terminal,
                    None,
                    f"oscillator:{module.bridge_id}:{record.endpoint_key}:gnd-stub",
                    _route_avoid_elements([*existing_items, *items], self.project.symbol_library),
                    [*existing_items, *items],
                    start_side=side,
                )
            )
        for record in bridge_top_ground_records:
            point = placed_bridge.ports[record.endpoint_key]
            symbol_point = (_snap(point[0]), _snap(point[1] - GRID * 2))
            items.extend(
                _wire_items_from_points(
                    self.sheet_path,
                    "GND",
                    [point, symbol_point],
                    record.terminal,
                    None,
                    f"oscillator:{module.bridge_id}:{record.endpoint_key}:top-gnd-stub",
                )
            )
            value_at, justify = _power_port_value_position(gnd_text, symbol_point, "NORTH")
            items.append(
                power_port_symbol(
                    self.sheet_path,
                    "GND",
                    f"oscillator:{module.bridge_id}:{record.endpoint_key}:top-gnd",
                    Point(symbol_point[0], symbol_point[1]),
                    Point(value_at[0], value_at[1]),
                    value=gnd_text,
                    justify=justify,
                    symbol_rotation=_power_port_symbol_rotation("NORTH"),
                    project_name=self.project.name,
                    sheet_instance_path=sheet_instance_path(self.sheet_path),
                )
            )
            if self._claim_implicit_power_driver("GND"):
                items.append(
                    power_driver_symbol(
                        self.sheet_path,
                        "GND",
                        f"oscillator:{module.bridge_id}:{record.endpoint_key}:top-gnd",
                        Point(symbol_point[0], symbol_point[1]),
                        project_name=self.project.name,
                        sheet_instance_path=sheet_instance_path(self.sheet_path),
                    )
                )

        wire_items = self._route_or_label_wire_requests(
            wire_requests,
            [*existing_items, *items],
            bridge_occupied,
        )
        items.extend(wire_items)
        gnd_anchor = (rail_left, ground_y)
        value_at, justify = _power_port_value_position(gnd_text, gnd_anchor, "WEST")
        items.append(
            power_port_symbol(
                self.sheet_path,
                "GND",
                f"oscillator:{module.bridge_id}:gnd",
                Point(gnd_anchor[0], gnd_anchor[1]),
                Point(value_at[0], value_at[1]),
                value=gnd_text,
                justify=justify,
                symbol_rotation=_power_port_symbol_rotation("WEST"),
                project_name=self.project.name,
                sheet_instance_path=sheet_instance_path(self.sheet_path),
            )
        )
        if self._claim_implicit_power_driver("GND"):
            items.append(
                power_driver_symbol(
                    self.sheet_path,
                    "GND",
                    f"oscillator:{module.bridge_id}:gnd",
                    Point(gnd_anchor[0], gnd_anchor[1]),
                    project_name=self.project.name,
                    sheet_instance_path=sheet_instance_path(self.sheet_path),
                )
            )
        return items, placed, connected, component_ids

    def _place_oscillator_bridge(
        self,
        module: _OscillatorModule,
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> PlacedComponent | None:
        component = self.components[module.bridge_id]
        vector = _side_vector(module.side)
        port_edges = [
            _port_body_edge(
                placed_root,
                root_record.endpoint_key,
                module.side,
                self.project.symbol_library,
            )
            for _net_name, root_record, _bridge_record in module.links
        ]
        port_edge = min(port_edges) if module.side == "WEST" else max(port_edges)
        best: tuple[float, PlacedComponent] | None = None
        occupied_index = _RectIndex(occupied)
        for rotation in (0, 90, 180, 270):
            facing_links = [
                link
                for link in module.links
                if _rotated_side(component.ports[link[2].endpoint_key].side, rotation) == _opposite_side(module.side)
            ]
            anchor_link = facing_links[0] if facing_links else module.links[0]
            _net_name, root_record, bridge_record = anchor_link
            root_point = placed_root.ports[root_record.endpoint_key]
            bridge_port = component.ports[bridge_record.endpoint_key]
            for distance in (SUPPORT_GAP, SUPPORT_STEP * 2, SUPPORT_STEP * 3):
                target = (
                    _snap(port_edge + vector[0] * distance),
                    _snap(root_point[1]),
                )
                at = _component_at_for_port(component, bridge_port, target, rotation)
                placed = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
                inflated = _inflate(placed.rect, GRID)
                overlap = _indexed_overlap_area(inflated, occupied_index)
                link_distance = 0.0
                side_mismatch = 0
                for _link_net, link_root_record, link_bridge_record in module.links:
                    root_link_point = placed_root.ports[link_root_record.endpoint_key]
                    bridge_link_point = placed.ports[link_bridge_record.endpoint_key]
                    link_distance += _manhattan(root_link_point, bridge_link_point)
                    if placed.port_sides[link_bridge_record.endpoint_key] not in {module.side, _opposite_side(module.side)}:
                        side_mismatch += 1
                score = overlap * 1_000_000.0 + side_mismatch * 100_000.0 + link_distance
                if best is None or score < best[0]:
                    best = (score, placed)
        return best[1] if best is not None else None

    def _place_indirect_support_component(
        self,
        component_id: str,
        placed_components: dict[str, PlacedComponent],
        occupied: list[Rect],
        *,
        outward_peer_ids: set[str] | None = None,
    ) -> tuple[PlacedComponent, str, NetEndpoint, NetEndpoint] | None:
        link = self._indirect_support_link(component_id, placed_components)
        if link is None:
            return None
        net_name, peer_record, support_record = link
        outward_peer_ids = outward_peer_ids or set()
        peer = placed_components[peer_record.component_id]
        peer_point = peer.ports[peer_record.endpoint_key]
        component = self.components[component_id]
        support_port = component.ports[support_record.endpoint_key]
        best: tuple[float, PlacedComponent] | None = None
        preferred_side = peer.port_sides[peer_record.endpoint_key]
        candidate_sides: tuple[PortSide, ...]
        target_side = preferred_side
        if peer_record.component_id in outward_peer_ids:
            target_side = _opposite_side(preferred_side)
            candidate_sides = (
                target_side,
                *_perpendicular_sides(preferred_side),
                preferred_side,
            )
        elif peer.component.passive:
            candidate_sides = (
                preferred_side,
                *_perpendicular_sides(preferred_side),
                _opposite_side(preferred_side),
            )
        else:
            candidate_sides = (preferred_side,)
        occupied_index = _RectIndex(occupied)
        for side in candidate_sides:
            rotation = _rotation_between_sides(support_port.side, _opposite_side(side))
            vector = _side_vector(side)
            perpendicular = _perp_vector(side)
            side_penalty = 0.0 if side == target_side else 50_000.0
            for distance in (SUPPORT_GAP, SUPPORT_STEP, SUPPORT_STEP * 2, SUPPORT_STEP * 3):
                for lane in _lane_offsets(limit=5):
                    target = (
                        _snap(peer_point[0] + vector[0] * distance + perpendicular[0] * lane),
                        _snap(peer_point[1] + vector[1] * distance + perpendicular[1] * lane),
                    )
                    at = _component_at_for_port(component, support_port, target, rotation)
                    placed = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
                    inflated = _inflate(placed.rect, GRID)
                    overlap = _indexed_overlap_area(inflated, occupied_index)
                    support_point = placed.ports[support_record.endpoint_key]
                    distance_score = _manhattan(peer_point, support_point)
                    score = overlap * 1_000_000.0 + abs(lane) * 100.0 + distance_score * 10.0 + distance + side_penalty
                    if best is None or score < best[0]:
                        best = (score, placed)
        if best is None:
            return None
        return best[1], net_name, peer_record, support_record

    def _indirect_support_link(
        self,
        component_id: str,
        placed_components: dict[str, PlacedComponent],
    ) -> tuple[str, NetEndpoint, NetEndpoint] | None:
        options: list[tuple[tuple[int, float, str, str], str, NetEndpoint, NetEndpoint]] = []
        for net_name, records in sorted(self.net_records.items()):
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            support_records = [record for record in records if record.component_id == component_id]
            peer_records = [record for record in records if record.component_id in placed_components]
            for support_record in support_records:
                for peer_record in peer_records:
                    peer_component = self.components[peer_record.component_id]
                    peer_point = placed_components[peer_record.component_id].ports[peer_record.endpoint_key]
                    score = (
                        0 if peer_component.passive else 1,
                        peer_point[0] + peer_point[1],
                        net_name,
                        support_record.endpoint_key,
                    )
                    options.append((score, net_name, peer_record, support_record))
        if not options:
            return None
        _score, net_name, peer_record, support_record = min(options, key=lambda item: item[0])
        return net_name, peer_record, support_record

    def _owned_root_endpoint_keys(self, root_id: str, owned_ids: set[str]) -> set[str]:
        endpoints: set[str] = set()
        if not owned_ids:
            return endpoints
        for records in self.net_records.values():
            root_records = [record for record in records if record.component_id == root_id]
            if not root_records:
                continue
            if any(record.component_id in owned_ids for record in records):
                endpoints.update(record.endpoint_key for record in root_records)
        return endpoints

    def _root_label_reservations(
        self,
        root_id: str,
        placed_root: PlacedComponent,
        *,
        excluded_endpoints: set[str],
    ) -> list[Rect]:
        reservations: list[Rect] = []
        seen: set[tuple[str, str]] = set()
        for net_name, records in sorted(self.net_records.items(), key=lambda item: (len(item[1]), item[0])):
            for record in records:
                key = (net_name, record.endpoint_key)
                if record.component_id != root_id or record.endpoint_key in excluded_endpoints or key in seen:
                    continue
                seen.add(key)
                point = placed_root.ports.get(record.endpoint_key)
                if point is None:
                    continue
                side = self.components[root_id].ports[record.endpoint_key].side
                anchor, justify = _axis_label_anchor(net_name, point, side, LABEL_GAP + 4 * GRID)
                label_rect = text_rect(Point(anchor[0], anchor[1]), net_name, justify=justify)
                reservations.append(_inflate(label_rect, GRID / 2))
                reservations.append(_inflate(_segment_rect(point, anchor), GRID / 3))
        return reservations

    def _island_internal_root_endpoints(self, root_id: str, island_peer_ids: frozenset[str]) -> set[str]:
        if not island_peer_ids:
            return set()
        endpoints: set[str] = set()
        for net_name, records in self.net_records.items():
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            has_peer = any(record.component_id in island_peer_ids for record in records)
            if not has_peer:
                continue
            endpoints.update(record.endpoint_key for record in records if record.component_id == root_id)
        return endpoints

    def _island_assembly(
        self,
        active_ids: tuple[str, ...],
        root_owned: dict[str, list[str]],
    ) -> Assembly:
        active_set = frozenset(active_ids)
        root_assemblies = {
            root_id: self._root_assembly(root_id, root_owned.get(root_id, []), active_set - {root_id})
            for root_id in active_ids
        }
        primary_id = max(
            active_ids,
            key=lambda component_id: (
                len(self.components[component_id].ports),
                self.components[component_id].kind == "symbol",
                component_id,
            ),
        )
        placements: dict[str, tuple[float, float]] = {primary_id: (0.0, 0.0)}
        placed_rects: dict[str, Rect] = {
            primary_id: Rect(0.0, 0.0, root_assemblies[primary_id].rect.width, root_assemblies[primary_id].rect.height)
        }
        pending = sorted(
            (component_id for component_id in active_ids if component_id != primary_id),
            key=lambda component_id: (-self._signal_connection_count(primary_id, component_id), component_id),
        )
        while pending:
            best: tuple[float, str, str, tuple[str, NetEndpoint, NetEndpoint], tuple[float, float]] | None = None
            for component_id in pending:
                for anchor_id in placements:
                    shared = self._shared_signal_records(anchor_id, component_id)
                    for link in shared:
                        _net_name, anchor_record, peer_record = link
                        side = root_assemblies[anchor_id].port_sides.get(
                            anchor_record.endpoint_key,
                            self.components[anchor_record.component_id].ports[anchor_record.endpoint_key].side,
                        )
                        placement = self._place_island_peer(
                            root_assemblies[component_id],
                            root_assemblies[anchor_id],
                            placed_rects[anchor_id],
                            root_assemblies[anchor_id].ports.get(anchor_record.endpoint_key),
                            root_assemblies[component_id].ports.get(peer_record.endpoint_key),
                            side,
                            list(placed_rects.values()),
                        )
                        score = self._island_peer_score(
                            root_assemblies[component_id],
                            placement,
                            list(placed_rects.values()),
                        )
                        score += self._island_link_distance(
                            root_assemblies[anchor_id],
                            placed_rects[anchor_id],
                            root_assemblies[component_id],
                            placement,
                            anchor_record,
                            peer_record,
                        )
                        score -= len(shared) * 1000.0
                        option = (score, anchor_id, component_id, link, placement)
                        if best is None or option[0] < best[0]:
                            best = option
            if best is None:
                component_id = pending.pop(0)
                anchor_id = primary_id
                shared = self._shared_signal_records(anchor_id, component_id)
                link = shared[0] if shared else self._synthetic_link(anchor_id, component_id)
                _net_name, anchor_record, peer_record = link
                side = root_assemblies[anchor_id].port_sides.get(
                    anchor_record.endpoint_key,
                    self.components[anchor_record.component_id].ports[anchor_record.endpoint_key].side,
                )
                placement = self._place_island_peer(
                    root_assemblies[component_id],
                    root_assemblies[anchor_id],
                    placed_rects[anchor_id],
                    root_assemblies[anchor_id].ports.get(anchor_record.endpoint_key),
                    root_assemblies[component_id].ports.get(peer_record.endpoint_key),
                    side,
                    list(placed_rects.values()),
                )
            else:
                _score, anchor_id, component_id, link, placement = best
                pending.remove(component_id)
            placements[component_id] = placement
            placed_rects[component_id] = Rect(
                placement[0],
                placement[1],
                placement[0] + root_assemblies[component_id].rect.width,
                placement[1] + root_assemblies[component_id].rect.height,
            )

        items: list[PlacedItem] = []
        ports: dict[str, tuple[float, float]] = {}
        port_sides: dict[str, PortSide] = {}
        component_ids: set[str] = set()
        for root_id in active_ids:
            assembly = root_assemblies[root_id]
            dx, dy = placements[root_id]
            items.extend(_translate_item(item, dx, dy) for item in assembly.items)
            for key, point in assembly.ports.items():
                ports[key] = _translate_point(point, dx, dy)
            port_sides.update(assembly.port_sides)
            component_ids.update(assembly.component_ids)
        items.extend(self._island_signal_items(active_set, ports, port_sides, items))
        rect = _items_rect(tuple(items), self.project.symbol_library) or Rect(0.0, 0.0, 0.0, 0.0)
        return _normalize_assembly(
            Assembly(
                "island:" + ":".join(active_ids),
                tuple(items),
                rect,
                ports,
                port_sides,
                frozenset(component_ids),
            )
        )

    def _signal_connection_count(self, first_id: str, second_id: str) -> int:
        return len(self._shared_signal_records(first_id, second_id))

    def _shared_signal_records(
        self,
        first_id: str,
        second_id: str,
    ) -> list[tuple[str, NetEndpoint, NetEndpoint]]:
        shared: list[tuple[str, NetEndpoint, NetEndpoint]] = []
        for net_name, records in sorted(self.net_records.items(), key=lambda item: (len(item[1]), item[0])):
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            first_records = [record for record in records if record.component_id == first_id]
            second_records = [record for record in records if record.component_id == second_id]
            for first_record in first_records:
                for second_record in second_records:
                    shared.append((net_name, first_record, second_record))
        return shared

    def _synthetic_link(self, anchor_id: str, peer_id: str) -> tuple[str, NetEndpoint, NetEndpoint]:
        anchor_port = next(iter(self.components[anchor_id].ports.values()))
        peer_port = next(iter(self.components[peer_id].ports.values()))
        return (
            "",
            NetEndpoint("", anchor_id, anchor_port.endpoint_key, anchor_port.terminal),
            NetEndpoint("", peer_id, peer_port.endpoint_key, peer_port.terminal),
        )

    def _place_island_peer(
        self,
        peer: Assembly,
        anchor: Assembly,
        anchor_rect: Rect,
        anchor_point: tuple[float, float] | None,
        peer_point: tuple[float, float] | None,
        side: PortSide,
        placed_rects: list[Rect],
    ) -> tuple[float, float]:
        if anchor_point is None:
            anchor_point = (anchor.rect.width / 2, anchor.rect.height / 2)
        if peer_point is None:
            peer_point = (peer.rect.width / 2, peer.rect.height / 2)
        anchor_abs = (anchor_rect.left + anchor_point[0], anchor_rect.top + anchor_point[1])
        if side == "WEST":
            base = (
                _snap(anchor_rect.left - ISLAND_GAP - peer.rect.width),
                _snap(anchor_abs[1] - peer_point[1]),
            )
            step = (0.0, _snap(peer.rect.height + ISLAND_GAP))
        elif side == "EAST":
            base = (
                _snap(anchor_rect.right + ISLAND_GAP),
                _snap(anchor_abs[1] - peer_point[1]),
            )
            step = (0.0, _snap(peer.rect.height + ISLAND_GAP))
        elif side == "NORTH":
            base = (
                _snap(anchor_abs[0] - peer_point[0]),
                _snap(anchor_rect.top - ISLAND_GAP - peer.rect.height),
            )
            step = (_snap(peer.rect.width + ISLAND_GAP), 0.0)
        else:
            base = (
                _snap(anchor_abs[0] - peer_point[0]),
                _snap(anchor_rect.bottom + ISLAND_GAP),
            )
            step = (_snap(peer.rect.width + ISLAND_GAP), 0.0)
        candidates = [base]
        for index in range(1, 12):
            candidates.append((_snap(base[0] + step[0] * index), _snap(base[1] + step[1] * index)))
            candidates.append((_snap(base[0] - step[0] * index), _snap(base[1] - step[1] * index)))
        best: tuple[float, tuple[float, float]] | None = None
        for x, y in candidates:
            rect = Rect(x, y, x + peer.rect.width, y + peer.rect.height)
            overlap = sum(_overlap_area(rect, placed) for placed in placed_rects)
            distance = abs(x - base[0]) + abs(y - base[1])
            score = overlap * 100_000.0 + distance
            if best is None or score < best[0]:
                best = (score, (x, y))
            if overlap <= 0.001:
                return (x, y)
        assert best is not None
        return best[1]

    def _island_peer_score(
        self,
        peer: Assembly,
        placement: tuple[float, float],
        placed_rects: list[Rect],
    ) -> float:
        x, y = placement
        rect = Rect(x, y, x + peer.rect.width, y + peer.rect.height)
        overlap = sum(_overlap_area(rect, placed) for placed in placed_rects)
        left = min([rect.left, *(placed.left for placed in placed_rects)])
        top = min([rect.top, *(placed.top for placed in placed_rects)])
        right = max([rect.right, *(placed.right for placed in placed_rects)])
        bottom = max([rect.bottom, *(placed.bottom for placed in placed_rects)])
        width = right - left
        height = bottom - top
        overflow = max(0.0, width - 400.0) + max(0.0, height - 280.0)
        return overlap * 1_000_000.0 + overflow * 100_000.0 + width * height

    def _island_link_distance(
        self,
        anchor: Assembly,
        anchor_rect: Rect,
        peer: Assembly,
        peer_placement: tuple[float, float],
        anchor_record: NetEndpoint,
        peer_record: NetEndpoint,
    ) -> float:
        anchor_point = anchor.ports.get(anchor_record.endpoint_key)
        peer_point = peer.ports.get(peer_record.endpoint_key)
        if anchor_point is None or peer_point is None:
            return 0.0
        anchor_abs = (anchor_rect.left + anchor_point[0], anchor_rect.top + anchor_point[1])
        peer_abs = (peer_placement[0] + peer_point[0], peer_placement[1] + peer_point[1])
        return abs(anchor_abs[0] - peer_abs[0]) + abs(anchor_abs[1] - peer_abs[1])

    def _island_signal_items(
        self,
        active_ids: frozenset[str],
        ports: dict[str, tuple[float, float]],
        port_sides: dict[str, PortSide],
        existing_items: list[PlacedItem],
    ) -> list[PlacedItem]:
        items: list[PlacedItem] = []
        route_requests: list[tuple[_WireRequest, NetEndpoint, NetEndpoint]] = []
        occupied = [
            _inflate(box.rect, GRID / 2)
            for box in placed_items_geometry(tuple(existing_items), symbol_library=self.project.symbol_library).boxes
        ]
        labeled: set[tuple[str, str]] = set()
        for net_name, records in sorted(self.net_records.items()):
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            local = [record for record in records if record.component_id in active_ids and record.endpoint_key in ports]
            if len(local) < 2:
                continue
            root = _local_net_root(local, self.components)
            root_point = ports[root.endpoint_key]
            for record in local:
                if record.endpoint_key == root.endpoint_key:
                    continue
                point = ports[record.endpoint_key]
                distance = abs(root_point[0] - point[0]) + abs(root_point[1] - point[1])
                if net_name in self.sheet.interface or distance > DIRECT_ISLAND_WIRE_LIMIT:
                    for endpoint in (root, record):
                        label_key = (net_name, endpoint.endpoint_key)
                        if label_key in labeled:
                            continue
                        labeled.add(label_key)
                        endpoint_point = ports[endpoint.endpoint_key]
                        component = self.components[endpoint.component_id]
                        side = port_sides.get(endpoint.endpoint_key, component.ports[endpoint.endpoint_key].side)
                        kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
                        items.extend(
                            _label_items(
                                self.sheet_path,
                                net_name,
                                self._label_text(net_name, kind),
                                endpoint_point,
                                side,
                                kind,
                                occupied,
                                endpoint.terminal,
                                f"island:{endpoint.endpoint_key}:{net_name}",
                                axis_locked=component.kind == "sheet" or not component.passive,
                                existing_items=[*existing_items, *items],
                                symbol_library=self.project.symbol_library,
                        )
                    )
                    continue
                route_requests.append(
                    (
                        _WireRequest(
                            net_name,
                            root_point,
                            point,
                            root.terminal,
                            record.terminal,
                            f"island:{root.component_id}:{record.component_id}:{record.endpoint_key}:{net_name}",
                            port_sides.get(root.endpoint_key, self.components[root.component_id].ports[root.endpoint_key].side),
                            port_sides.get(record.endpoint_key, self.components[record.component_id].ports[record.endpoint_key].side),
                        ),
                        root,
                        record,
                    )
                )
        for request, root, record in sorted(route_requests, key=lambda item: _wire_request_route_order(item[0])):
            path_context = _path_context(
                _route_avoid_elements([*existing_items, *items], self.project.symbol_library),
                _existing_wire_segments([*existing_items, *items]),
            )
            candidates = _route_candidates_for_request(0, request, path_context)
            choice = None
            if candidates:
                candidate = candidates[0]
                choice = _path_choice(
                    list(candidate.points),
                    path_context,
                    net_name=request.net_name,
                    start_terminal=request.start_terminal,
                    end_terminal=request.end_terminal,
                    start_side=request.start_side,
                    end_side=request.end_side,
                )
            if choice is not None and choice.blockers == 0 and choice.contacts == 0:
                wire_items = _wire_items_from_points(
                    self.sheet_path,
                    request.net_name,
                    list(choice.points),
                    request.start_terminal,
                    request.end_terminal,
                    request.key,
                )
                items.extend(wire_items)
                occupied.extend(_wire_avoid_rects(wire_items))
                continue
            for endpoint in (root, record):
                label_key = (request.net_name, endpoint.endpoint_key)
                if label_key in labeled:
                    continue
                labeled.add(label_key)
                endpoint_point = ports[endpoint.endpoint_key]
                component = self.components[endpoint.component_id]
                side = port_sides.get(endpoint.endpoint_key, component.ports[endpoint.endpoint_key].side)
                label_items = _label_items(
                    self.sheet_path,
                    request.net_name,
                    self._label_text(request.net_name, "local"),
                    endpoint_point,
                    side,
                    "local",
                    occupied,
                    endpoint.terminal,
                    f"island:{endpoint.endpoint_key}:{request.net_name}",
                    axis_locked=component.kind == "sheet" or not component.passive,
                    existing_items=[*existing_items, *items],
                    symbol_library=self.project.symbol_library,
                )
                items.extend(label_items)
                occupied.extend(_wire_avoid_rects(label_items))
        return items

    def _side_lanes(
        self,
        root_id: str,
        owned_ids: list[str],
        placed_root: PlacedComponent,
    ) -> dict[PortSide, list[tuple[str, NetEndpoint, NetEndpoint]]]:
        lanes: dict[PortSide, list[tuple[str, NetEndpoint, NetEndpoint]]] = {"WEST": [], "EAST": [], "NORTH": [], "SOUTH": []}
        for component_id in owned_ids:
            link = self._best_passive_root_link(root_id, component_id, placed_root)
            if link is None:
                continue
            side, peer_record, passive_record = link
            lanes[side].append((component_id, peer_record, passive_record))
        for side, lane in lanes.items():
            lanes[side] = sorted(
                lane,
                key=lambda item: (
                    placed_root.ports[item[1].endpoint_key][1] if side in {"WEST", "EAST"} else placed_root.ports[item[1].endpoint_key][0],
                    item[0],
                ),
            )
        return lanes

    def _best_passive_root_link(
        self,
        root_id: str,
        passive_id: str,
        placed_root: PlacedComponent,
    ) -> tuple[PortSide, NetEndpoint, NetEndpoint] | None:
        choices: list[tuple[tuple[int, float, str, str], PortSide, NetEndpoint, NetEndpoint]] = []
        for net_name, records in sorted(self.net_records.items()):
            if _is_ground_net(net_name):
                continue
            component = self.components[passive_id]
            if _is_power_net(net_name) and len(component.ports) > 2:
                continue
            passive_records = [record for record in records if record.component_id == passive_id]
            peer_records = [record for record in records if record.component_id == root_id]
            if not passive_records or not peer_records:
                continue
            for peer_record in peer_records:
                side = self.components[root_id].ports[peer_record.endpoint_key].side
                point = placed_root.ports[peer_record.endpoint_key]
                axis = point[1] if side in {"WEST", "EAST"} else point[0]
                for passive_record in passive_records:
                    score = (
                        1 if _is_power_net(net_name) else 0,
                        axis,
                        net_name,
                        passive_record.endpoint_key,
                    )
                    choices.append((score, side, peer_record, passive_record))
        if not choices:
            return None
        _score, side, peer_record, passive_record = min(choices, key=lambda item: item[0])
        return side, peer_record, passive_record

    def _support_bridge_modules(
        self,
        root_id: str,
        owned_ids: list[str],
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> tuple[list[PlacedItem], dict[str, PlacedComponent], set[str], set[str], list[_WireRequest]]:
        items: list[PlacedItem] = []
        placed: dict[str, PlacedComponent] = {}
        connected: set[str] = set()
        component_ids: set[str] = set()
        wire_requests: list[_WireRequest] = []
        for component_id in sorted(owned_ids):
            bridge = self._root_signal_bridge(root_id, component_id, placed_root, occupied)
            if bridge is None:
                continue
            support_placement, links = bridge
            placed[component_id] = support_placement
            component_ids.add(component_id)
            items.extend(support_placement.items)
            occupied.extend(_occupied_rects(support_placement.items, self.project.symbol_library, margin=GRID / 2))
            for net_name, root_record, support_record in links:
                root_point = placed_root.ports[root_record.endpoint_key]
                support_point = support_placement.ports[support_record.endpoint_key]
                if net_name not in self.sheet.interface:
                    wire_requests.append(
                        _WireRequest(
                            net_name,
                            root_point,
                            support_point,
                            root_record.terminal,
                            support_record.terminal,
                            f"assembly:{root_id}:{component_id}:bridge:{net_name}",
                            placed_root.port_sides[root_record.endpoint_key],
                            support_placement.port_sides[support_record.endpoint_key],
                        )
                    )
                    connected.update({root_record.endpoint_key, support_record.endpoint_key})
        return items, placed, connected, component_ids, wire_requests

    def _root_signal_bridge(
        self,
        root_id: str,
        component_id: str,
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> tuple[PlacedComponent, tuple[tuple[str, NetEndpoint, NetEndpoint], ...]] | None:
        component = self.components[component_id]
        links: list[tuple[str, NetEndpoint, NetEndpoint]] = []
        seen_nets: set[str] = set()
        for net_name, records in sorted(self.net_records.items()):
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            support_records = [record for record in records if record.component_id == component_id]
            root_records = [record for record in records if record.component_id == root_id]
            if not support_records or not root_records or net_name in seen_nets:
                continue
            seen_nets.add(net_name)
            links.append((net_name, root_records[0], support_records[0]))
        if len(links) < 2:
            return None
        root_sides = [placed_root.port_sides[root_record.endpoint_key] for _net_name, root_record, _support_record in links]
        side = max(("WEST", "EAST", "NORTH", "SOUTH"), key=lambda candidate: (root_sides.count(candidate), -("WEST", "EAST", "NORTH", "SOUTH").index(candidate)))
        same_side_links = [
            link
            for link in links
            if placed_root.port_sides[link[1].endpoint_key] == side
        ]
        if len(same_side_links) < 2:
            return None
        links = sorted(
            same_side_links,
            key=lambda link: (
                placed_root.ports[link[1].endpoint_key][1] if side in {"WEST", "EAST"} else placed_root.ports[link[1].endpoint_key][0],
                link[0],
            ),
        )
        port_edge = _port_body_edge(
            placed_root,
            links[0][1].endpoint_key,
            side,
            self.project.symbol_library,
        )
        best: tuple[float, PlacedComponent] | None = None
        root_points = [placed_root.ports[root_record.endpoint_key] for _net_name, root_record, _support_record in links]
        occupied_index = _RectIndex(occupied)
        for rotation in (0, 90, 180, 270):
            first_support_port = component.ports[links[0][2].endpoint_key]
            for column in range(3):
                for axis_delta in _lane_offsets(limit=4):
                    if side == "WEST":
                        target = (
                            _snap(port_edge - SUPPORT_GAP - column * BANK_COLUMN_STEP),
                            _snap(root_points[0][1] + axis_delta),
                        )
                    elif side == "EAST":
                        target = (
                            _snap(port_edge + SUPPORT_GAP + column * BANK_COLUMN_STEP),
                            _snap(root_points[0][1] + axis_delta),
                        )
                    elif side == "NORTH":
                        target = (
                            _snap(root_points[0][0] + axis_delta),
                            _snap(port_edge - SUPPORT_GAP - column * BANK_COLUMN_STEP),
                        )
                    else:
                        target = (
                            _snap(root_points[0][0] + axis_delta),
                            _snap(port_edge + SUPPORT_GAP + column * BANK_COLUMN_STEP),
                        )
                    at = _component_at_for_port(component, first_support_port, target, rotation)
                    placed = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
                    inflated = _inflate(placed.rect, GRID)
                    overlap = _indexed_overlap_area(inflated, occupied_index)
                    side_mismatch = sum(
                        1
                        for _net_name, _root_record, support_record in links
                        if placed.port_sides[support_record.endpoint_key] != _opposite_side(side)
                    )
                    link_distance = sum(
                        _manhattan(placed_root.ports[root_record.endpoint_key], placed.ports[support_record.endpoint_key])
                        for _net_name, root_record, support_record in links
                    )
                    spread_error = 0.0
                    for index, (_net_name, root_record, support_record) in enumerate(links):
                        root_point = placed_root.ports[root_record.endpoint_key]
                        support_point = placed.ports[support_record.endpoint_key]
                        if side in {"WEST", "EAST"}:
                            spread_error += abs(root_point[1] - support_point[1])
                        else:
                            spread_error += abs(root_point[0] - support_point[0])
                    score = (
                        overlap * 1_000_000.0
                        + side_mismatch * 100_000.0
                        + column * 2_000.0
                        + abs(axis_delta) * 250.0
                        + spread_error * 100.0
                        + link_distance
                    )
                    if best is None or score < best[0]:
                        best = (score, placed)
        if best is None:
            return None
        return best[1], tuple(links)

    def _shares_signal_with_any(self, component_id: str, peer_ids: set[str]) -> bool:
        if not peer_ids:
            return False
        for net_name, records in self.net_records.items():
            if _is_ground_net(net_name) or _is_power_net(net_name):
                continue
            has_component = any(record.component_id == component_id for record in records)
            if not has_component:
                continue
            if any(record.component_id in peer_ids for record in records):
                return True
        return False

    def _support_rail_modules(
        self,
        root_id: str,
        side_lanes: dict[PortSide, list[tuple[str, NetEndpoint, NetEndpoint]]],
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> tuple[list[PlacedItem], dict[str, PlacedComponent], set[str], set[str]]:
        items: list[PlacedItem] = []
        placed: dict[str, PlacedComponent] = {}
        connected: set[str] = set()
        component_ids: set[str] = set()

        for side in ("NORTH", "SOUTH"):
            net_groups: dict[str, list[tuple[str, NetEndpoint, NetEndpoint, NetEndpoint]]] = {}
            for component_id, peer_record, passive_record in side_lanes[side]:
                component = self.components[component_id]
                ground_record = self._ground_record(component_id)
                if ground_record is None or len(component.ports) != 2:
                    continue
                net_groups.setdefault(passive_record.net_name, []).append(
                    (component_id, peer_record, passive_record, ground_record)
                )

            rail_index = 0
            for net_name, group in sorted(net_groups.items(), key=lambda item: (-len(item[1]), item[0])):
                if len(group) < 2:
                    continue
                root_records = [
                    record
                    for record in self.net_records.get(net_name, [])
                    if record.component_id == root_id
                    and self.components[root_id].ports[record.endpoint_key].side == side
                ]
                if not root_records:
                    continue
                desired_side = _opposite_side(side)
                direction = -1.0 if side == "NORTH" else 1.0
                net_y = _snap((placed_root.rect.top if side == "NORTH" else placed_root.rect.bottom) + direction * (SUPPORT_GAP + rail_index * RAIL_STACK_STEP))
                root_xs = [placed_root.ports[record.endpoint_key][0] for record in root_records]
                center_x = (min(root_xs) + max(root_xs)) / 2
                profiles: list[tuple[str, NetEndpoint, NetEndpoint, NetEndpoint, int, Rect]] = []
                for component_id, peer_record, passive_record, ground_record in group:
                    component = self.components[component_id]
                    rotation = _rotation_between_sides(
                        component.ports[passive_record.endpoint_key].side,
                        desired_side,
                    )
                    span = self._component_span_for_port(component, passive_record, rotation)
                    profiles.append((component_id, peer_record, passive_record, ground_record, rotation, span))
                total_width = _rail_profile_width([profile[-1] for profile in profiles])
                cursor = _snap(center_x - total_width / 2)
                net_points: list[tuple[float, float]] = []
                ground_points: list[tuple[float, float]] = []
                ground_records: list[NetEndpoint] = []
                passive_records: list[NetEndpoint] = []
                for component_id, _peer_record, passive_record, ground_record, rotation, span in profiles:
                    component = self.components[component_id]
                    x = _snap(cursor - span.left)
                    at = _component_at_for_port(component, component.ports[passive_record.endpoint_key], (x, net_y), rotation)
                    placed_component = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
                    placed[component_id] = placed_component
                    component_ids.add(component_id)
                    items.extend(placed_component.items)
                    occupied.extend(_occupied_rects(placed_component.items, self.project.symbol_library, margin=GRID / 2))
                    net_points.append(placed_component.ports[passive_record.endpoint_key])
                    ground_points.append(placed_component.ports[ground_record.endpoint_key])
                    passive_records.append(passive_record)
                    ground_records.append(ground_record)
                    cursor = _snap(x + span.right + GRID * 2)

                rail_left = _snap(min(point[0] for point in net_points))
                rail_right = _snap(max(point[0] for point in net_points))
                wire_items = _rail_wire_items(
                    self.sheet_path,
                    net_name,
                    [(rail_left, net_y), *net_points, (rail_right, net_y)],
                    set(net_points),
                    f"support-rail:{root_id}:{side}:{net_name}:trunk",
                )
                items.extend(wire_items)
                occupied.extend(_wire_avoid_rects(wire_items))
                for record in passive_records:
                    connected.add(record.endpoint_key)

                ground_y = _snap(sum(point[1] for point in ground_points) / len(ground_points))
                ground_left = _snap(min(point[0] for point in ground_points))
                ground_right = _snap(max(point[0] for point in ground_points))
                wire_items = _rail_wire_items(
                    self.sheet_path,
                    "GND",
                    [(ground_left, ground_y), *ground_points, (ground_right, ground_y)],
                    set(ground_points),
                    f"support-rail:{root_id}:{side}:{net_name}:gnd",
                )
                items.extend(wire_items)
                occupied.extend(_wire_avoid_rects(wire_items))
                for record in ground_records:
                    connected.add(record.endpoint_key)
                items.extend(
                    _power_port_items(
                        self.project,
                        self.sheet_path,
                        net_name,
                        self._power_label_text(net_name),
                        (rail_right, net_y),
                        "EAST",
                        occupied,
                        f"support-rail:{root_id}:{side}:{net_name}",
                        axis_locked=True,
                        driven=self._claim_implicit_power_driver(net_name),
                        existing_items=items,
                        symbol_library=self.project.symbol_library,
                    )
                )
                items.extend(
                    _power_port_items(
                        self.project,
                        self.sheet_path,
                        "GND",
                        self._power_label_text("GND"),
                        (ground_left, ground_y),
                        "WEST",
                        occupied,
                        f"support-rail:{root_id}:{side}:{net_name}:gnd",
                        axis_locked=True,
                        driven=self._claim_implicit_power_driver("GND"),
                        existing_items=items,
                        symbol_library=self.project.symbol_library,
                    )
                )
                rail_index += 1

        return items, placed, connected, component_ids

    def _ground_record(self, component_id: str) -> NetEndpoint | None:
        for net_name, records in self.net_records.items():
            if not _is_ground_net(net_name):
                continue
            for record in records:
                if record.component_id == component_id:
                    return record
        return None

    def _has_power_return(self, component_id: str, signal_endpoint_key: str) -> bool:
        records = [
            record
            for net_records in self.net_records.values()
            for record in net_records
            if record.component_id == component_id
        ]
        endpoint_keys = {record.endpoint_key for record in records}
        if len(endpoint_keys) != 2 or signal_endpoint_key not in endpoint_keys:
            return False
        return any(
            record.endpoint_key != signal_endpoint_key and _is_power_net(record.net_name)
            for record in records
        )

    def _component_span_for_port(
        self,
        component: Component,
        record: NetEndpoint,
        rotation: int,
    ) -> Rect:
        port = component.ports[record.endpoint_key]
        at = _component_at_for_port(component, port, (0.0, 0.0), rotation)
        placed = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
        return placed.rect

    def _place_side_passive_bank(
        self,
        side: PortSide,
        lanes: list[tuple[str, NetEndpoint, NetEndpoint]],
        placed_components: dict[str, PlacedComponent],
        occupied: list[Rect],
        existing_items: list[PlacedItem],
    ) -> _PassiveBankState:
        if not lanes:
            return _PassiveBankState(
                0.0,
                (),
                (),
                (),
                (),
                frozenset(),
                frozenset(),
            )

        side_count = len(lanes)
        states = [
            _PassiveBankState(
                0.0,
                (),
                (),
                (),
                (),
                frozenset(),
                frozenset(),
            )
        ]
        beam_width = 8
        candidate_limit = 6
        for component_id, peer_record, passive_record in lanes:
            component = self.components[component_id]
            next_states: list[_PassiveBankState] = []
            for state in states:
                state_items = [*existing_items, *state.items]
                state_occupied = [*occupied, *state.occupied]
                candidates = self._passive_bank_candidates(
                    component,
                    placed_components[peer_record.component_id],
                    peer_record,
                    passive_record,
                    side,
                    side_count,
                    state_occupied,
                    existing_items=state_items,
                    prior_wire_requests=state.wire_requests,
                    limit=candidate_limit,
                )
                for candidate in candidates:
                    connected = set(state.connected)
                    wire_requests = list(state.wire_requests)
                    if candidate.wire_request is not None:
                        wire_requests.append(candidate.wire_request)
                        connected.update({peer_record.endpoint_key, passive_record.endpoint_key})
                    next_states.append(
                        _PassiveBankState(
                            state.score + candidate.score,
                            (*state.items, *candidate.placed.items),
                            (*state.occupied, *candidate.occupied),
                            (*state.placed, candidate.placed),
                            tuple(wire_requests),
                            frozenset(connected),
                            frozenset((*state.component_ids, component_id)),
                        )
                    )
            states = sorted(next_states, key=lambda state: state.score)[:beam_width]
        return min(states, key=lambda state: state.score)

    def _passive_bank_candidates(
        self,
        component: Component,
        peer: PlacedComponent,
        peer_record: NetEndpoint,
        passive_record: NetEndpoint,
        side: PortSide,
        side_count: int,
        occupied: list[Rect],
        *,
        existing_items: list[PlacedItem] | None = None,
        prior_wire_requests: tuple[_WireRequest, ...] = (),
        limit: int | None = None,
    ) -> list[_PassiveBankCandidate]:
        placed_root = peer
        peer_point = placed_root.ports[peer_record.endpoint_key]
        passive_port = component.ports[passive_record.endpoint_key]
        raw_candidates: list[tuple[float, tuple[float, float], int, _WireRequest | None]] = []
        max_columns = 3 if side_count > 6 else 2
        axis_limit = 4 if side_count <= 8 else 6
        shunt_support = self._has_power_return(component.id, passive_record.endpoint_key)
        existing_segments = _existing_wire_segments(existing_items or [])
        occupied_index = _RectIndex(occupied)
        rotation = _rotation_between_sides(passive_port.side, _opposite_side(side))
        port_edge = _port_body_edge(
            placed_root,
            peer_record.endpoint_key,
            side,
            self.project.symbol_library,
        )
        for column in range(max_columns):
            for axis_delta in _port_axis_offsets(limit=axis_limit):
                if side == "WEST":
                    target = (
                        _snap(port_edge - SUPPORT_GAP - column * BANK_COLUMN_STEP),
                        _snap(peer_point[1] + axis_delta),
                    )
                elif side == "EAST":
                    target = (
                        _snap(port_edge + SUPPORT_GAP + column * BANK_COLUMN_STEP),
                        _snap(peer_point[1] + axis_delta),
                    )
                elif side == "NORTH":
                    target = (
                        _snap(peer_point[0] + axis_delta),
                        _snap(port_edge - SUPPORT_GAP - column * BANK_COLUMN_STEP),
                    )
                else:
                    target = (
                        _snap(peer_point[0] + axis_delta),
                        _snap(port_edge + SUPPORT_GAP + column * BANK_COLUMN_STEP),
                    )
                at = _component_at_for_port(component, passive_port, target, rotation)
                placed = self._candidate_component_geometry(component, Point(at[0], at[1]), rotation, compact_value=True)
                inflated = _inflate(placed.rect, GRID)
                overlap = _indexed_overlap_area(inflated, occupied_index)
                passive_point = placed.ports[passive_record.endpoint_key]
                distance = _manhattan(peer_point, passive_point)
                route_score = 0.0
                wire_request: _WireRequest | None = None
                if (
                    existing_items is not None
                    and not _is_power_net(passive_record.net_name)
                    and passive_record.net_name not in self.sheet.interface
                    and _direct_support_wire_allowed(
                        placed_root,
                        placed,
                        passive_record.net_name,
                        peer_point,
                        passive_point,
                    )
                ):
                    wire_request = _WireRequest(
                        passive_record.net_name,
                        peer_point,
                        passive_point,
                        peer_record.terminal,
                        passive_record.terminal,
                        f"assembly:{peer.component.id}:{component.id}:{passive_record.net_name}",
                        placed_root.port_sides[peer_record.endpoint_key],
                        placed.port_sides[passive_record.endpoint_key],
                    )
                    route_score = _provisional_wire_score(
                        prior_wire_requests,
                        wire_request,
                        existing_segments=existing_segments,
                    )
                column_score = abs(column - 1) * 2_000.0 if shunt_support and max_columns > 1 else column * 2_000.0
                score = (
                    overlap * 100_000.0
                    + route_score
                    + abs(axis_delta) * 100.0
                    + distance * 10.0
                    + column_score
                )
                raw_candidates.append(
                    (score, at, rotation, wire_request)
                )
        candidates: list[_PassiveBankCandidate] = []
        for score, at, rotation, wire_request in sorted(raw_candidates, key=lambda candidate: candidate[0])[:limit]:
            placed = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
            candidates.append(
                    _PassiveBankCandidate(
                        placed,
                        wire_request,
                        tuple(_occupied_rects(placed.items, self.project.symbol_library, margin=GRID / 2)),
                        score,
                    )
                )
        return candidates

    def _shared_rail_cap_ids(self) -> set[str]:
        groups: dict[tuple[str, str], list[str]] = {}
        for component_id in sorted(self.components):
            rail_cap = self._rail_cap_record(component_id)
            if rail_cap is None:
                continue
            groups.setdefault((rail_cap.rail_record.net_name, rail_cap.ground_record.net_name), []).append(component_id)
        return {
            component_id
            for group in groups.values()
            if len(group) >= 2
            for component_id in group
        }

    def _root_rail_cap_bank_modules(
        self,
        root_id: str,
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> tuple[list[PlacedItem], set[str], set[str]]:
        shared_cap_ids = self._shared_rail_cap_ids()
        groups: dict[tuple[str, str], list[_RailCap]] = {}
        for component_id in sorted(shared_cap_ids):
            if self._direct_owner(component_id) != root_id:
                continue
            rail_cap = self._rail_cap_record(component_id)
            if rail_cap is None:
                continue
            groups.setdefault((rail_cap.rail_record.net_name, rail_cap.ground_record.net_name), []).append(rail_cap)

        items: list[PlacedItem] = []
        connected: set[str] = set()
        component_ids: set[str] = set()
        for (rail_name, ground_name), group in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(group) < 2:
                continue
            assembly = self._shared_rail_cap_bank_assembly(rail_name, ground_name, group)
            dx, dy = self._root_cap_bank_placement(assembly, placed_root, occupied)
            translated_items = [_translate_item(item, dx, dy) for item in assembly.items]
            items.extend(translated_items)
            occupied.extend(_occupied_rects(tuple(translated_items), self.project.symbol_library, margin=GRID / 2))
            component_ids.update(assembly.component_ids)
            for rail_cap in group:
                connected.add(rail_cap.rail_record.endpoint_key)
                connected.add(rail_cap.ground_record.endpoint_key)
        return items, connected, component_ids

    def _root_cap_bank_placement(
        self,
        assembly: Assembly,
        placed_root: PlacedComponent,
        occupied: list[Rect],
    ) -> tuple[float, float]:
        visible_boxes, route_boxes = _assembly_pack_boxes(assembly, self.project.symbol_library)
        boxes = [*visible_boxes, *route_boxes]
        occupied_index = _RectIndex(occupied)
        root_center_x = (placed_root.rect.left + placed_root.rect.right) / 2
        root_center_y = (placed_root.rect.top + placed_root.rect.bottom) / 2
        candidates: list[tuple[str, float, float, float]] = []
        for distance_index in range(8):
            distance = SUPPORT_GAP + distance_index * CAP_BANK_ROW_GAP
            for lane in _lane_offsets(limit=4):
                candidates.append(
                    (
                        "NORTH",
                        _snap(root_center_x - assembly.rect.width / 2 + lane),
                        _snap(placed_root.rect.top - distance - assembly.rect.height),
                        distance + abs(lane),
                    )
                )
                candidates.append(
                    (
                        "SOUTH",
                        _snap(root_center_x - assembly.rect.width / 2 + lane),
                        _snap(placed_root.rect.bottom + distance),
                        distance + abs(lane),
                    )
                )
            for lane in _lane_offsets(limit=2):
                candidates.append(
                    (
                        "WEST",
                        _snap(placed_root.rect.left - distance - assembly.rect.width),
                        _snap(root_center_y - assembly.rect.height / 2 + lane),
                        1000.0 + distance + abs(lane),
                    )
                )
                candidates.append(
                    (
                        "EAST",
                        _snap(placed_root.rect.right + distance),
                        _snap(root_center_y - assembly.rect.height / 2 + lane),
                        1000.0 + distance + abs(lane),
                    )
                )
        best: tuple[float, tuple[float, float]] | None = None
        for side, x, y, distance_penalty in candidates:
            translated = [_translate_rect(box, x, y) for box in boxes]
            overlap = 0.0
            for box in translated:
                for rect in occupied_index.query(box):
                    overlap += _overlap_area(box, rect)
            side_penalty = {
                "SOUTH": 0.0,
                "EAST": 1_000.0,
                "WEST": 1_000.0,
                "NORTH": 100_000.0,
            }[side]
            score = overlap * 1_000_000.0 + side_penalty + distance_penalty
            option = (score, (x, y))
            if best is None or option[0] < best[0]:
                best = option
        assert best is not None
        return best[1]

    def _shared_rail_cap_bank_assemblies(self, placed: set[str]) -> list[Assembly]:
        groups: dict[tuple[str, str], list[_RailCap]] = {}
        for component_id in sorted(self.components):
            if component_id in placed:
                continue
            rail_cap = self._rail_cap_record(component_id)
            if rail_cap is None:
                continue
            groups.setdefault((rail_cap.rail_record.net_name, rail_cap.ground_record.net_name), []).append(rail_cap)
        assemblies: list[Assembly] = []
        for (rail_name, ground_name), group in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(group) < 2:
                continue
            assemblies.append(self._shared_rail_cap_bank_assembly(rail_name, ground_name, group))
        return assemblies

    def _rail_cap_record(self, component_id: str) -> _RailCap | None:
        component = self.components[component_id]
        if not _is_decoupling_cap(component):
            return None
        records = [
            record
            for net_records in self.net_records.values()
            for record in net_records
            if record.component_id == component_id
        ]
        rail_records = [
            record
            for record in records
            if _is_power_net(record.net_name) and not _is_ground_net(record.net_name)
        ]
        ground_records = [record for record in records if _is_ground_net(record.net_name)]
        if len(rail_records) != 1 or len(ground_records) != 1:
            return None
        return _RailCap(component_id, rail_records[0], ground_records[0])

    def _shared_rail_cap_bank_assembly(
        self,
        rail_name: str,
        ground_name: str,
        group: list[_RailCap],
    ) -> Assembly:
        items: list[PlacedItem] = []
        occupied: list[Rect] = []
        placed: dict[str, PlacedComponent] = {}
        profiles: list[tuple[_RailCap, int, Rect]] = []
        for rail_cap in group:
            component = self.components[rail_cap.component_id]
            rotation = _rotation_between_sides(
                component.ports[rail_cap.rail_record.endpoint_key].side,
                "NORTH",
            )
            span = self._component_span_for_port(component, rail_cap.rail_record, rotation)
            profiles.append((rail_cap, rotation, span))

        rows: list[list[tuple[_RailCap, int, Rect]]] = []
        row: list[tuple[_RailCap, int, Rect]] = []
        row_width = 0.0
        for profile in profiles:
            span = profile[2]
            next_width = span.width if not row else row_width + GRID * 2 + span.width
            if row and next_width > CAP_BANK_MAX_WIDTH:
                rows.append(row)
                row = []
                row_width = 0.0
                next_width = span.width
            row.append(profile)
            row_width = next_width
        if row:
            rows.append(row)

        row_y = 0.0
        rail_points: list[tuple[float, float]] = []
        ground_points: list[tuple[float, float]] = []
        row_rails: list[tuple[float, float, float, tuple[tuple[float, float], ...]]] = []
        row_grounds: list[tuple[float, float, float, tuple[tuple[float, float], ...]]] = []
        for current_row in rows:
            cursor = 0.0
            row_rail_points: list[tuple[float, float]] = []
            row_ground_points: list[tuple[float, float]] = []
            for rail_cap, rotation, span in current_row:
                component = self.components[rail_cap.component_id]
                x = _snap(cursor - span.left)
                at = _component_at_for_port(
                    component,
                    component.ports[rail_cap.rail_record.endpoint_key],
                    (x, row_y),
                    rotation,
                )
                placed_component = self._place_component(component, Point(at[0], at[1]), rotation, compact_value=True)
                placed[rail_cap.component_id] = placed_component
                items.extend(placed_component.items)
                occupied.extend(_occupied_rects(placed_component.items, self.project.symbol_library, margin=GRID / 2))
                rail_point = placed_component.ports[rail_cap.rail_record.endpoint_key]
                ground_point = placed_component.ports[rail_cap.ground_record.endpoint_key]
                rail_points.append(rail_point)
                ground_points.append(ground_point)
                row_rail_points.append(rail_point)
                row_ground_points.append(ground_point)
                cursor = _snap(x + span.right + GRID * 2)

            row_left = _snap(min(point[0] for point in row_rail_points) - GRID * 2)
            row_right = _snap(max(point[0] for point in row_ground_points) + GRID * 2)
            rail_y = _snap(min(point[1] for point in row_rail_points))
            ground_y = _snap(max(point[1] for point in row_ground_points))
            row_rails.append((row_left, row_right, rail_y, tuple(row_rail_points)))
            row_grounds.append((row_left, row_right, ground_y, tuple(row_ground_points)))
            row_y = _snap(ground_y + CAP_BANK_ROW_GAP)

        for left, right, rail_y, taps in row_rails:
            wire_items = _rail_wire_items(
                self.sheet_path,
                rail_name,
                [(left, rail_y), *taps],
                set(taps),
                f"shared-cap-bank:{rail_name}:trunk:{rail_y}",
            )
            items.extend(wire_items)
            occupied.extend(_wire_avoid_rects(wire_items))
        for left, right, ground_y, taps in row_grounds:
            wire_items = _rail_wire_items(
                self.sheet_path,
                ground_name,
                [*taps, (right, ground_y)],
                set(taps),
                f"shared-cap-bank:{rail_name}:gnd:{ground_y}",
            )
            items.extend(wire_items)
            occupied.extend(_wire_avoid_rects(wire_items))

        for index, (left, _right, rail_y, _taps) in enumerate(row_rails):
            items.extend(
                _power_port_items(
                    self.project,
                    self.sheet_path,
                    rail_name,
                    self._power_label_text(rail_name),
                    (left, rail_y),
                    "WEST",
                    occupied,
                    f"shared-cap-bank:{rail_name}:row:{index}",
                    axis_locked=True,
                    driven=self._claim_implicit_power_driver(rail_name),
                    existing_items=items,
                    symbol_library=self.project.symbol_library,
                )
            )
        for index, (_left, right, ground_y, _taps) in enumerate(row_grounds):
            items.extend(
                _power_port_items(
                    self.project,
                    self.sheet_path,
                    ground_name,
                    self._power_label_text(ground_name),
                    (right, ground_y),
                    "EAST",
                    occupied,
                    f"shared-cap-bank:{rail_name}:gnd:row:{index}",
                    axis_locked=True,
                    driven=self._claim_implicit_power_driver(ground_name),
                    existing_items=items,
                    symbol_library=self.project.symbol_library,
                )
            )
        rect = _items_rect(tuple(items), self.project.symbol_library) or Rect(0.0, 0.0, 0.0, 0.0)
        assembly_id = f"shared-cap-bank:{rail_name}:{ground_name}"
        component_ids = frozenset(cap.component_id for cap in group)
        return _normalize_assembly(
            Assembly(
                assembly_id,
                tuple(items),
                rect,
                _ports_for(placed),
                _port_sides_for(placed),
                component_ids,
            )
        )

    def _assembly_net_items(
        self,
        component_ids: set[str],
        placed: dict[str, PlacedComponent],
        occupied: list[Rect],
        connected_endpoints: set[str],
        *,
        base_items_extra: list[PlacedItem] | None = None,
    ) -> list[PlacedItem]:
        items: list[PlacedItem] = []
        base_items = [item for component in placed.values() for item in component.items]
        if base_items_extra is not None:
            base_items.extend(base_items_extra)
            occupied.extend(_wire_avoid_rects(base_items_extra))
        wire_requests: list[_WireRequest] = []
        label_requests: list[_LabelRequest] = []
        power_port_requests: list[tuple[str, str, tuple[float, float], PortSide, str, bool]] = []
        for net_name, records in sorted(self.net_records.items()):
            local = [record for record in records if record.component_id in component_ids]
            if not local:
                continue
            external = [record for record in records if record.component_id not in component_ids]
            unconnected = [record for record in local if record.endpoint_key not in connected_endpoints]
            external_bridge_records = self._external_bridge_records(local, connected_endpoints) if external else []
            for rail_items, rail_records in self._repeated_net_rails(
                net_name,
                unconnected,
                placed,
                occupied,
                [*base_items, *items],
            ):
                items.extend(rail_items)
                connected_endpoints.update(record.endpoint_key for record in rail_records)
            unconnected = [record for record in unconnected if record.endpoint_key not in connected_endpoints]
            marker_records = [
                *unconnected,
                *(
                    record
                    for record in external_bridge_records
                    if record.endpoint_key not in {candidate.endpoint_key for candidate in unconnected}
                ),
            ]
            if net_name in self.sheet.interface:
                interface_records = marker_records
                fallback_interface_label = False
                if not interface_records and _is_power_net(net_name):
                    connected_interface = [
                        record
                        for record in local
                        if record.endpoint_key in connected_endpoints
                    ]
                    interface_records = connected_interface[:1] or local[:1]
                    fallback_interface_label = True
                if not interface_records:
                    continue
                for record in interface_records:
                    point = placed[record.component_id].ports[record.endpoint_key]
                    component = self.components[record.component_id]
                    label_requests.append(
                        _LabelRequest(
                            net_name,
                            self._label_text(net_name, "hierarchical"),
                            point,
                            placed[record.component_id].port_sides[record.endpoint_key],
                            "hierarchical",
                            record.terminal,
                            record.endpoint_key,
                            fallback_interface_label or component.kind == "sheet" or not component.passive,
                        )
                    )
                    connected_endpoints.add(record.endpoint_key)
                continue
            if not _is_power_net(net_name) and len(local) >= 2 and not external:
                root_record = _local_net_root(local, self.components)
                root_point = placed[root_record.component_id].ports[root_record.endpoint_key]
                local_support_net = self._local_support_net(local)
                if local_support_net:
                    connected_local = [
                        record
                        for record in local
                        if record.endpoint_key in connected_endpoints
                    ]
                    anchors = connected_local or [root_record]
                    anchor = anchors[0]
                    point = placed[anchor.component_id].ports[anchor.endpoint_key]
                    component = self.components[anchor.component_id]
                    label_requests.append(
                        _LabelRequest(
                            net_name,
                            self._label_text(net_name, "local"),
                            point,
                            placed[anchor.component_id].port_sides[anchor.endpoint_key],
                            "local",
                            anchor.terminal,
                            anchor.endpoint_key,
                            component.kind == "sheet" or not component.passive,
                        )
                    )
                    labeled: set[str] = set()
                    for record in local:
                        if record.endpoint_key in connected_endpoints or record.endpoint_key == root_record.endpoint_key:
                            continue
                        point = placed[record.component_id].ports[record.endpoint_key]
                        anchor = min(
                            anchors,
                            key=lambda candidate: _manhattan(
                                placed[candidate.component_id].ports[candidate.endpoint_key],
                                point,
                            ),
                        )
                        anchor_point = placed[anchor.component_id].ports[anchor.endpoint_key]
                        if _direct_support_wire_allowed(
                            placed[anchor.component_id],
                            placed[record.component_id],
                            net_name,
                            anchor_point,
                            point,
                        ):
                            wire_requests.append(
                                _WireRequest(
                                    net_name,
                                    anchor_point,
                                    point,
                                    anchor.terminal,
                                    record.terminal,
                                    f"assembly:{anchor.component_id}:{record.component_id}:{net_name}",
                                    placed[anchor.component_id].port_sides[anchor.endpoint_key],
                                    placed[record.component_id].port_sides[record.endpoint_key],
                                )
                            )
                        else:
                            for endpoint in (anchor, record):
                                if endpoint.endpoint_key in labeled:
                                    continue
                                labeled.add(endpoint.endpoint_key)
                                component = self.components[endpoint.component_id]
                                kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
                                label_requests.append(
                                    _LabelRequest(
                                        net_name,
                                        self._label_text(net_name, kind),
                                        placed[endpoint.component_id].ports[endpoint.endpoint_key],
                                        placed[endpoint.component_id].port_sides[endpoint.endpoint_key],
                                        kind,
                                        endpoint.terminal,
                                        endpoint.endpoint_key,
                                        component.kind == "sheet" or not component.passive,
                                    )
                                )
                        connected_endpoints.update({anchor.endpoint_key, record.endpoint_key})
                    continue
                if self._net_all_passive(net_name) and not local_support_net:
                    for record in local:
                        point = placed[record.component_id].ports[record.endpoint_key]
                        component = self.components[record.component_id]
                        kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
                        label_requests.append(
                            _LabelRequest(
                                net_name,
                                self._label_text(net_name, kind),
                                point,
                                placed[record.component_id].port_sides[record.endpoint_key],
                                kind,
                                record.terminal,
                                record.endpoint_key,
                                component.kind == "sheet" or not component.passive,
                            )
                        )
                        connected_endpoints.add(record.endpoint_key)
                    continue
                long_local = any(
                    record.endpoint_key != root_record.endpoint_key
                    and record.endpoint_key not in connected_endpoints
                    and _manhattan(root_point, placed[record.component_id].ports[record.endpoint_key]) > DIRECT_LOCAL_WIRE_LIMIT
                    for record in local
                )
                if long_local and not local_support_net:
                    for record in local:
                        point = placed[record.component_id].ports[record.endpoint_key]
                        component = self.components[record.component_id]
                        kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
                        label_requests.append(
                            _LabelRequest(
                                net_name,
                                self._label_text(net_name, kind),
                                point,
                                placed[record.component_id].port_sides[record.endpoint_key],
                                kind,
                                record.terminal,
                                record.endpoint_key,
                                component.kind == "sheet" or not component.passive,
                            )
                        )
                        connected_endpoints.add(record.endpoint_key)
                    continue
                for record in local:
                    if record.endpoint_key == root_record.endpoint_key or record.endpoint_key in connected_endpoints:
                        continue
                    point = placed[record.component_id].ports[record.endpoint_key]
                    wire_requests.append(
                        _WireRequest(
                            net_name,
                            root_point,
                            point,
                            root_record.terminal,
                            record.terminal,
                            f"assembly:{root_record.component_id}:{record.component_id}:{net_name}",
                            placed[root_record.component_id].port_sides[root_record.endpoint_key],
                            placed[record.component_id].port_sides[record.endpoint_key],
                        )
                    )
                    connected_endpoints.add(record.endpoint_key)
                    connected_endpoints.add(root_record.endpoint_key)
                continue
            for record in marker_records:
                point = placed[record.component_id].ports[record.endpoint_key]
                component = self.components[record.component_id]
                side = placed[record.component_id].port_sides[record.endpoint_key]
                kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
                axis_locked = component.kind == "sheet" or not component.passive
                use_power_port = (
                    _is_power_net(net_name)
                    and component.kind != "sheet"
                    and net_name not in self.sheet.interface
                )
                if use_power_port:
                    power_port_requests.append(
                        (
                            net_name,
                            self._power_label_text(net_name),
                            point,
                            side,
                            record.endpoint_key,
                            axis_locked,
                        )
                    )
                else:
                    label_requests.append(
                        _LabelRequest(
                            net_name,
                            self._label_text(net_name, kind),
                            point,
                            side,
                            kind,
                            record.terminal,
                            record.endpoint_key,
                            axis_locked,
                        )
                    )
        label_path_context = (
            _path_context(
                _route_avoid_elements([*base_items, *items], self.project.symbol_library),
                _existing_wire_segments([*base_items, *items]),
            )
            if label_requests
            else None
        )
        for request in label_requests:
            label_items = _label_items(
                self.sheet_path,
                request.net_name,
                request.label_text,
                request.point,
                request.side,
                request.kind,
                occupied,
                request.terminal,
                request.endpoint_key,
                axis_locked=request.axis_locked,
                existing_items=[*base_items, *items],
                symbol_library=self.project.symbol_library,
                path_context=label_path_context,
            )
            items.extend(label_items)
            occupied.extend(_wire_avoid_rects(label_items))
        wire_items = self._route_or_label_wire_requests(
            wire_requests,
            [*base_items, *items],
            occupied,
        )
        items.extend(wire_items)
        hard_occupied = _occupied_rects([*base_items, *items], self.project.symbol_library, margin=GRID / 2)
        deduped_power_port_requests: dict[
            tuple[str, tuple[float, float]],
            tuple[str, str, tuple[float, float], PortSide, str, bool],
        ] = {}
        for request in power_port_requests:
            net_name, _label_text, point, _side, _endpoint_key, _axis_locked = request
            deduped_power_port_requests.setdefault((net_name, point), request)
        for net_name, label_text, point, side, endpoint_key, axis_locked in deduped_power_port_requests.values():
            new_items = _power_port_items(
                self.project,
                self.sheet_path,
                net_name,
                label_text,
                point,
                side,
                occupied,
                endpoint_key,
                axis_locked=axis_locked,
                hard_occupied=hard_occupied,
                driven=self._claim_implicit_power_driver(net_name),
                existing_items=[*base_items, *items],
                symbol_library=self.project.symbol_library,
            )
            items.extend(new_items)
            hard_occupied.extend(_occupied_rects(tuple(new_items), self.project.symbol_library, margin=GRID / 2))
        return items

    def _route_or_label_wire_requests(
        self,
        wire_requests: list[_WireRequest],
        existing_items: list[PlacedItem],
        occupied: list[Rect],
    ) -> list[PlacedItem]:
        items: list[PlacedItem] = []
        occupied.extend(_wire_avoid_rects(existing_items))
        labeled: set[tuple[str, tuple[float, float], str]] = set()
        avoid_elements = _route_avoid_elements(existing_items, self.project.symbol_library)
        existing_segments = _existing_wire_segments(existing_items)
        for request in sorted(wire_requests, key=_wire_request_route_order):
            path_context = _path_context(avoid_elements, existing_segments)
            candidates = _route_candidates_for_request(0, request, path_context)
            choice = None
            if candidates:
                candidate = candidates[0]
                choice = _path_choice(
                    list(candidate.points),
                    path_context,
                    net_name=request.net_name,
                    start_terminal=request.start_terminal,
                    end_terminal=request.end_terminal,
                    start_side=request.start_side,
                    end_side=request.end_side,
                )
            if choice is not None and choice.blockers == 0 and choice.contacts == 0:
                wire_items = _wire_items_from_points(
                    self.sheet_path,
                    request.net_name,
                    list(choice.points),
                    request.start_terminal,
                    request.end_terminal,
                    request.key,
                )
                items.extend(wire_items)
                occupied.extend(_wire_avoid_rects(wire_items))
                existing_segments.extend(_existing_wire_segments(wire_items))
                continue
            for suffix, point, terminal, side in (
                ("start", request.start, request.start_terminal, request.start_side),
                ("end", request.end, request.end_terminal, request.end_side),
            ):
                marker_key = (request.net_name, point, suffix)
                if marker_key in labeled:
                    continue
                labeled.add(marker_key)
                if _is_power_net(request.net_name):
                    marker_items = _power_port_items(
                        self.project,
                        self.sheet_path,
                        request.net_name,
                        self._power_label_text(request.net_name),
                        point,
                        side or "EAST",
                        occupied,
                        f"{request.key}:{suffix}",
                        axis_locked=True,
                        existing_items=[*existing_items, *items],
                        symbol_library=self.project.symbol_library,
                    )
                else:
                    marker_items = _label_items(
                        self.sheet_path,
                        request.net_name,
                        self._label_text(request.net_name, "local"),
                        point,
                        side or "EAST",
                        "local",
                        occupied,
                        terminal,
                        f"{request.key}:{suffix}",
                        axis_locked=False,
                        existing_items=[*existing_items, *items],
                        symbol_library=self.project.symbol_library,
                    )
                items.extend(marker_items)
                occupied.extend(_wire_avoid_rects(marker_items))
                avoid_elements.extend(_route_avoid_elements(marker_items, self.project.symbol_library))
                existing_segments.extend(_existing_wire_segments(marker_items))
        return items

    def _local_support_net(self, records: list[NetEndpoint]) -> bool:
        non_support = [
            record.component_id
            for record in records
            if not _is_local_support_component(self.components[record.component_id])
        ]
        return len(set(non_support)) <= 1

    def _external_bridge_records(
        self,
        local: list[NetEndpoint],
        connected_endpoints: set[str],
    ) -> list[NetEndpoint]:
        connected = [record for record in local if record.endpoint_key in connected_endpoints]
        if not connected:
            return []
        non_passive = [
            record
            for record in connected
            if not self.components[record.component_id].passive
        ]
        return [min(non_passive or connected, key=lambda record: record.endpoint_key)]

    def _net_all_passive(self, net_name: str) -> bool:
        records = self.net_records.get(net_name, [])
        if not records:
            return False
        return all(self._record_electrical_type(record) in {"passive", "unspecified"} for record in records)

    def _record_electrical_type(self, record: NetEndpoint) -> str:
        component = self.components.get(record.component_id)
        if component is None or component.kind != "symbol":
            return "non_passive"
        pin = component.ports.get(record.endpoint_key).pin if record.endpoint_key in component.ports else None
        return pin.electrical_type if pin is not None else "unspecified"

    def _claim_implicit_power_driver(self, net_name: str) -> bool:
        if not _is_power_net(net_name):
            return False
        if net_name in self._implicit_driver_nets:
            return False
        if self._project_net_has_driver(net_name):
            return False
        if self._first_sheet_for_net(net_name) != self.sheet_path:
            return False
        self._implicit_driver_nets.add(net_name)
        return True

    def _project_net_has_driver(self, net_name: str) -> bool:
        for sheet_path, sheet in self.project.source.sheets.items():
            if net_name in sheet.power_flags:
                return True
            resolved_sheet = self.project.sheets.get(sheet_path)
            if resolved_sheet is None:
                continue
            for endpoint in resolved_sheet.nets.get(net_name, []):
                if endpoint.kind is not EndpointKind.SYMBOL_PIN or endpoint.ref is None:
                    continue
                decl = sheet.symbols.get(endpoint.ref)
                info = self.project.symbol_library.get(decl.lib) if decl is not None else None
                pin = _resolved_endpoint_pin(info, endpoint)
                if pin is not None and pin.electrical_type == "power_out":
                    return True
        return False

    def _first_sheet_for_net(self, net_name: str) -> str | None:
        for sheet_path in sorted(self.project.sheets):
            if sheet_path == "/":
                continue
            if net_name in self.project.sheets[sheet_path].nets:
                return sheet_path
        if "/" in self.project.sheets and net_name in self.project.sheets["/"].nets:
            return "/"
        return None

    def _repeated_net_rails(
        self,
        net_name: str,
        records: list[NetEndpoint],
        placed: dict[str, PlacedComponent],
        occupied: list[Rect],
        existing_items: list[PlacedItem],
    ) -> list[tuple[list[PlacedItem], list[NetEndpoint]]]:
        groups: dict[tuple[str, PortSide, float], list[NetEndpoint]] = {}
        for record in records:
            placed_component = placed[record.component_id]
            side = placed_component.port_sides[record.endpoint_key]
            point = placed_component.ports[record.endpoint_key]
            side_axis = point[0] if side in {"WEST", "EAST"} else point[1]
            groups.setdefault((record.component_id, side, _snap(side_axis)), []).append(record)
        if _is_power_net(net_name):
            for key, group in self._passive_power_rail_groups(records, placed).items():
                groups[key] = group
        rails: list[tuple[list[PlacedItem], list[NetEndpoint]]] = []
        for (group_id, side, _side_axis), group in sorted(groups.items()):
            if len(group) < 2:
                continue
            minimum_group_size = 2 if _is_power_net(net_name) else 3
            if len(group) < minimum_group_size:
                continue
            runs = (
                _passive_power_rail_runs(group, side, placed)
                if group_id.startswith("passive-rail:")
                else _contiguous_rail_runs(group, side, placed)
            )
            if len(runs) > 1:
                for run in runs:
                    if len(run) >= minimum_group_size:
                        rails.extend(self._repeated_net_rails(net_name, list(run), placed, occupied, existing_items))
                continue
            points = [placed[record.component_id].ports[record.endpoint_key] for record in group]
            if side in {"WEST", "EAST"}:
                ys = sorted(point[1] for point in points)
                edge_x = min(point[0] for point in points) if side == "WEST" else max(point[0] for point in points)
                direction = -1.0 if side == "WEST" else 1.0
                rail_start, rail_end, rail_points = min(
                    (
                        _rail_lane_score(
                            [(point, (_snap(edge_x + direction * (LABEL_GAP + lane * GRID)), point[1])) for point in points],
                            (
                                (_snap(edge_x + direction * (LABEL_GAP + lane * GRID)), ys[0]),
                                (_snap(edge_x + direction * (LABEL_GAP + lane * GRID)), ys[-1]),
                            ),
                            occupied,
                        ),
                        (_snap(edge_x + direction * (LABEL_GAP + lane * GRID)), ys[0]),
                        (_snap(edge_x + direction * (LABEL_GAP + lane * GRID)), ys[-1]),
                        [( _snap(edge_x + direction * (LABEL_GAP + lane * GRID)), point[1]) for point in points],
                    )
                    for lane in range(16)
                )[1:]
                label_side = side
            else:
                xs = sorted(point[0] for point in points)
                edge_y = min(point[1] for point in points) if side == "NORTH" else max(point[1] for point in points)
                direction = -1.0 if side == "NORTH" else 1.0
                rail_start, rail_end, rail_points = min(
                    (
                        _rail_lane_score(
                            [(point, (point[0], _snap(edge_y + direction * (LABEL_GAP + lane * GRID)))) for point in points],
                            (
                                (xs[0], _snap(edge_y + direction * (LABEL_GAP + lane * GRID))),
                                (xs[-1], _snap(edge_y + direction * (LABEL_GAP + lane * GRID))),
                            ),
                            occupied,
                        ),
                        (xs[0], _snap(edge_y + direction * (LABEL_GAP + lane * GRID))),
                        (xs[-1], _snap(edge_y + direction * (LABEL_GAP + lane * GRID))),
                        [(point[0], _snap(edge_y + direction * (LABEL_GAP + lane * GRID))) for point in points],
                    )
                    for lane in range(16)
                )[1:]
                label_side = side
            items: list[PlacedItem] = []
            wire_items = _rail_wire_items(
                self.sheet_path,
                net_name,
                [rail_start, *rail_points, rail_end],
                set(rail_points),
                f"rail:{group_id}:{side}:{net_name}:trunk",
            )
            items.extend(wire_items)
            occupied.extend(_wire_avoid_rects(wire_items))
            for record, point, rail_point in zip(group, points, rail_points):
                wire_items = _wire_items_avoiding(
                    self.sheet_path,
                    net_name,
                    point,
                    rail_point,
                    record.terminal,
                    None,
                    f"rail:{group_id}:{side}:{record.endpoint_key}:{net_name}",
                    _route_avoid_elements([*existing_items, *items], self.project.symbol_library),
                    [*existing_items, *items],
                    start_side=side,
                )
                items.extend(wire_items)
                occupied.extend(_wire_avoid_rects(wire_items))
            if _is_power_net(net_name):
                items.extend(
                    _power_port_items(
                        self.project,
                        self.sheet_path,
                        net_name,
                        self._power_label_text(net_name),
                        rail_end,
                        label_side,
                        occupied,
                        f"rail:{group_id}:{side}:{net_name}",
                        axis_locked=True,
                        driven=self._claim_implicit_power_driver(net_name),
                        existing_items=[*existing_items, *items],
                        symbol_library=self.project.symbol_library,
                    )
                )
            else:
                label_kind: Literal["local", "hierarchical"] = "hierarchical" if net_name in self.sheet.interface else "local"
                items.extend(
                    _label_items(
                        self.sheet_path,
                        net_name,
                        self._label_text(net_name, label_kind),
                        rail_end,
                        label_side,
                        label_kind,
                        occupied,
                        None,
                        f"rail:{group_id}:{side}:{net_name}",
                        existing_items=[*existing_items, *items],
                        symbol_library=self.project.symbol_library,
                    )
                )
            rails.append((items, group))
        return rails

    def _passive_power_rail_groups(
        self,
        records: list[NetEndpoint],
        placed: dict[str, PlacedComponent],
    ) -> dict[tuple[str, PortSide, float], list[NetEndpoint]]:
        groups: dict[tuple[str, PortSide, float], list[NetEndpoint]] = {}
        for record in records:
            component = self.components[record.component_id]
            if not component.passive or len(component.ports) != 2:
                continue
            placed_component = placed[record.component_id]
            side = placed_component.port_sides[record.endpoint_key]
            point = placed_component.ports[record.endpoint_key]
            side_axis = _snap(point[0] if side in {"WEST", "EAST"} else point[1])
            groups.setdefault((f"passive-rail:{side}:{side_axis}", side, side_axis), []).append(record)
        return {key: group for key, group in groups.items() if len(group) >= 2}

    def _floating_assembly(self, component_ids: tuple[str, ...]) -> Assembly:
        items: list[PlacedItem] = []
        occupied: list[Rect] = []
        placed: dict[str, PlacedComponent] = {}
        cursor_x = 0.0
        y = 0.0
        row_height = 0.0
        for component_id in component_ids:
            component = self.components[component_id]
            sample = self._place_component(component, Point(0.0, 0.0), 0, compact_value=component.passive)
            if cursor_x > 0.001 and cursor_x + sample.rect.width > 180.0:
                cursor_x = 0.0
                y = _snap(y + row_height + SUPPORT_STEP)
                row_height = 0.0
            at = Point(
                _snap(cursor_x - sample.rect.left),
                _snap(y - sample.rect.top),
            )
            placed_component = self._place_component(component, at, 0, compact_value=component.passive)
            placed[component_id] = placed_component
            items.extend(placed_component.items)
            occupied.extend(_occupied_rects(placed_component.items, self.project.symbol_library, margin=GRID / 2))
            cursor_x = _snap(placed_component.rect.right + SUPPORT_STEP)
            row_height = max(row_height, placed_component.rect.height)
        items.extend(self._assembly_net_items(set(component_ids), placed, occupied, set()))
        rect = _items_rect(tuple(items), self.project.symbol_library) or Rect(0.0, 0.0, 0.0, 0.0)
        component_id_set = frozenset(component_ids)
        return _normalize_assembly(
            Assembly(
                ":".join(component_ids),
                tuple(items),
                rect,
                _ports_for(placed),
                _port_sides_for(placed),
                component_id_set,
            )
        )

    def _loose_marker_bank_assemblies(self, placed_ids: set[str]) -> list[Assembly]:
        groups: dict[str, list[str]] = {}
        for component_id, component in sorted(self.components.items()):
            if component_id in placed_ids or not _is_loose_marker_component(component):
                continue
            groups.setdefault(self._loose_marker_bank_key(component_id), []).append(component_id)
        return [
            self._loose_marker_bank_assembly(key, tuple(sorted(component_ids, key=self._loose_marker_sort_key)))
            for key, component_ids in sorted(groups.items())
            if len(component_ids) >= 2
        ]

    def _loose_marker_bank_key(self, component_id: str) -> str:
        text = self._loose_marker_sort_key(component_id)
        tokens = [token for token in re.split(r"[^A-Za-z0-9]+", text.upper()) if token]
        if not tokens:
            return component_id
        if tokens[0] == "USB" and len(tokens) >= 2:
            return "_".join(tokens[:2])
        if tokens[0] in {"HUB", "CONSOLE", "CARLINKIT", "MIC", "DAC"}:
            return tokens[0]
        return tokens[0]

    def _loose_marker_sort_key(self, component_id: str) -> str:
        component = self.components[component_id]
        net_names = sorted(
            net_name
            for net_name, records in self.net_records.items()
            if any(record.component_id == component_id for record in records)
        )
        if net_names:
            return self._label_text(net_names[0], "local")
        if component.symbol_decl is not None and component.symbol_decl.value:
            return component.symbol_decl.value
        return component.ref or component_id

    def _loose_marker_bank_assembly(self, bank_key: str, component_ids: tuple[str, ...]) -> Assembly:
        items: list[PlacedItem] = []
        occupied: list[Rect] = []
        placed: dict[str, PlacedComponent] = {}
        columns = 2 if len(component_ids) > 6 else 1
        rows_per_column = (len(component_ids) + columns - 1) // columns
        for index, component_id in enumerate(component_ids):
            component = self.components[component_id]
            column = index // rows_per_column
            row = index % rows_per_column
            sample = self._place_component(component, Point(0.0, 0.0), 0, compact_value=True)
            at = Point(
                _snap(column * MARKER_BANK_COLUMN_STEP - sample.rect.left),
                _snap(row * MARKER_BANK_ROW_STEP - sample.rect.top),
            )
            placed_component = self._place_component(component, at, 0, compact_value=True)
            placed[component_id] = placed_component
            items.extend(placed_component.items)
            occupied.extend(_occupied_rects(placed_component.items, self.project.symbol_library, margin=GRID / 2))
        items.extend(self._assembly_net_items(set(component_ids), placed, occupied, set()))
        rect = _items_rect(tuple(items), self.project.symbol_library) or Rect(0.0, 0.0, 0.0, 0.0)
        component_id_set = frozenset(component_ids)
        return _normalize_assembly(
            Assembly(
                f"markers:{bank_key}",
                tuple(items),
                rect,
                _ports_for(placed),
                _port_sides_for(placed),
                component_id_set,
            )
        )

    def _standalone_symbol_bank_assemblies(self, placed_ids: set[str]) -> list[Assembly]:
        groups: dict[tuple[str, str, str], list[str]] = {}
        for component_id, component in sorted(self.components.items()):
            if component_id in placed_ids or not self._is_standalone_symbol(component_id):
                continue
            groups.setdefault(self._standalone_symbol_bank_key(component), []).append(component_id)
        return [
            self._standalone_symbol_bank_assembly(key, tuple(sorted(component_ids, key=_component_ref_sort_key)))
            for key, component_ids in sorted(groups.items())
            if len(component_ids) >= 2
        ]

    def _is_standalone_symbol(self, component_id: str) -> bool:
        component = self.components[component_id]
        if component.kind != "symbol" or component.ref is None or component.symbol_decl is None:
            return False
        if component.ports:
            return False
        return not any(
            record.component_id == component_id
            for records in self.net_records.values()
            for record in records
        )

    def _standalone_symbol_bank_key(self, component: Component) -> tuple[str, str, str]:
        assert component.symbol_decl is not None
        return (
            component.symbol_decl.lib,
            component.symbol_decl.value or "",
            component.symbol_decl.footprint or "",
        )

    def _standalone_symbol_bank_assembly(
        self,
        bank_key: tuple[str, str, str],
        component_ids: tuple[str, ...],
    ) -> Assembly:
        items: list[PlacedItem] = []
        placed: dict[str, PlacedComponent] = {}
        samples = {
            component_id: self._place_component(
                self.components[component_id],
                Point(0.0, 0.0),
                0,
                compact_value=True,
            )
            for component_id in component_ids
        }
        max_width = max((sample.rect.width for sample in samples.values()), default=MARKER_BANK_COLUMN_STEP)
        max_height = max((sample.rect.height for sample in samples.values()), default=MARKER_BANK_ROW_STEP)
        column_step = _snap(max(SUPPORT_STEP * 2, max_width + GRID * 2))
        row_step = _snap(max(SUPPORT_STEP * 2, max_height + GRID * 2))
        columns = min(4, len(component_ids))
        for index, component_id in enumerate(component_ids):
            component = self.components[component_id]
            sample = samples[component_id]
            column = index % columns
            row = index // columns
            at = Point(
                _snap(column * column_step - sample.rect.left),
                _snap(row * row_step - sample.rect.top),
            )
            placed_component = self._place_component(component, at, 0, compact_value=True)
            placed[component_id] = placed_component
            items.extend(placed_component.items)
        rect = _items_rect(tuple(items), self.project.symbol_library) or Rect(0.0, 0.0, 0.0, 0.0)
        return _normalize_assembly(
            Assembly(
                f"standalone:{'|'.join(bank_key)}",
                tuple(items),
                rect,
                _ports_for(placed),
                _port_sides_for(placed),
                frozenset(component_ids),
            )
        )

    def _place_component(
        self,
        component: Component,
        at: Point,
        rotation: int,
        *,
        compact_value: bool = False,
    ) -> PlacedComponent:
        prototype = self._component_prototype(component, rotation, compact_value)
        if abs(at.x) < 0.001 and abs(at.y) < 0.001:
            return prototype
        if component.kind == "symbol":
            assert component.ref is not None and component.unit is not None and component.symbol_decl is not None
            item = _placed_symbol(self.project, self.sheet_path, component.ref, component.unit, component.symbol_decl, component.symbol_info, at, rotation, compact_value=compact_value)
            items: tuple[PlacedItem, ...] = (item,)
        elif component.kind == "sheet":
            assert component.sheet_block is not None
            items = (_translate_item(component.sheet_block, at.x, at.y),)
        else:
            assert component.power_net is not None and component.power_index is not None
            items = (
                power_flag_symbol(
                    self.sheet_path,
                    component.power_net,
                    component.power_index,
                    at,
                    project_name=self.project.name,
                    sheet_instance_path=sheet_instance_path(self.sheet_path),
                ),
            )
        ports = {
            endpoint_key: _component_port_point(component, port, at, rotation)
            for endpoint_key, port in component.ports.items()
        }
        if component.kind == "symbol":
            items = (*items, *self._component_no_connects(component, ports))
        return PlacedComponent(
            component,
            at,
            prototype.rotation,
            items,
            ports,
            prototype.port_sides,
            _translate_rect(prototype.rect, at.x, at.y),
        )

    def _candidate_component_geometry(
        self,
        component: Component,
        at: Point,
        rotation: int,
        *,
        compact_value: bool = False,
    ) -> PlacedComponent:
        prototype = self._component_prototype(component, rotation, compact_value)
        return PlacedComponent(
            component,
            at,
            prototype.rotation,
            (),
            {key: _translate_point(point, at.x, at.y) for key, point in prototype.ports.items()},
            prototype.port_sides,
            _translate_rect(prototype.rect, at.x, at.y),
        )

    def _component_prototype(
        self,
        component: Component,
        rotation: int,
        compact_value: bool,
    ) -> PlacedComponent:
        key = (component.id, rotation % 360, compact_value)
        cached = self._placed_component_cache.get(key)
        if cached is not None:
            return cached
        at = Point(0.0, 0.0)
        if component.kind == "symbol":
            assert component.ref is not None and component.unit is not None and component.symbol_decl is not None
            item = _placed_symbol(self.project, self.sheet_path, component.ref, component.unit, component.symbol_decl, component.symbol_info, at, rotation, compact_value=compact_value)
            items: tuple[PlacedItem, ...] = (item,)
        elif component.kind == "sheet":
            assert component.sheet_block is not None
            items = (_translate_item(component.sheet_block, at.x, at.y),)
        else:
            assert component.power_net is not None and component.power_index is not None
            items = (
                power_flag_symbol(
                    self.sheet_path,
                    component.power_net,
                    component.power_index,
                    at,
                    project_name=self.project.name,
                    sheet_instance_path=sheet_instance_path(self.sheet_path),
                ),
            )
        ports = {
            endpoint_key: _component_port_point(component, port, at, rotation)
            for endpoint_key, port in component.ports.items()
        }
        port_sides = {
            endpoint_key: _rotated_side(port.side, rotation)
            for endpoint_key, port in component.ports.items()
        }
        if component.kind == "symbol":
            items = (*items, *self._component_no_connects(component, ports))
        rect = _items_rect(items, self.project.symbol_library) or Rect(at.x, at.y, at.x, at.y)
        placed = PlacedComponent(component, at, rotation % 360, items, ports, port_sides, rect)
        self._placed_component_cache[key] = placed
        return placed

    def _pack_assemblies(self, assemblies: list[Assembly]) -> tuple[list[PlacedItem], dict[str, tuple[float, float]]]:
        content = usable_page_rect_for_paper(PAPER)
        if content is None:
            return [], {}
        comfort = Rect(
            content.left + PAGE_COMFORT_MARGIN,
            content.top + PAGE_COMFORT_MARGIN,
            content.right - PAGE_COMFORT_MARGIN,
            content.bottom - PAGE_COMFORT_MARGIN,
        )
        title_block = title_block_rect_for_paper(PAPER)
        initial_blockers = (title_block,) if title_block is not None else ()
        ordered, placements = _best_pack_placements(
            assemblies,
            (comfort, content),
            self.project.symbol_library,
            initial_blockers,
        )
        placed_rects: list[Rect] = []
        overflow: list[str] = []
        for assembly in ordered:
            placement = placements.get(assembly.id)
            if placement is None:
                overflow.append(assembly.id)
                placement = (content.left, content.bottom + PACK_GAP + len(overflow) * PACK_GAP)
            placements[assembly.id] = placement
            placed_rects.append(
                Rect(
                    placement[0] - PACK_GAP,
                    placement[1] - PACK_GAP,
                    placement[0] + assembly.rect.width + PACK_GAP,
                    placement[1] + assembly.rect.height + PACK_GAP,
                )
            )
        if overflow:
            self.layout_errors.append(f"{self.sheet_path}: {len(overflow)} assemblies exceed A3 content area")

        items: list[PlacedItem] = []
        ports: dict[str, tuple[float, float]] = {}
        for assembly in assemblies:
            dx, dy = placements[assembly.id]
            items.extend(_translate_item(item, dx, dy) for item in assembly.items)
            for key, point in assembly.ports.items():
                ports[key] = _translate_point(point, dx, dy)
        return items, ports

    def _component_no_connects(
        self,
        component: Component,
        ports: dict[str, tuple[float, float]],
    ) -> tuple[PlacedNoConnect, ...]:
        if component.kind != "symbol" or component.ref is None or component.unit is None:
            return ()
        items: list[PlacedItem] = []
        for index, endpoint_text in enumerate(self.sheet.no_connects):
            try:
                endpoint = parse_endpoint(endpoint_text)
            except ValueError:
                continue
            if endpoint.kind is not EndpointKind.SYMBOL_PIN or endpoint.ref != component.ref:
                continue
            pins = _source_endpoint_pins(component.symbol_info, endpoint)
            if not pins:
                continue
            for pin in pins:
                unit = pin.unit if pin.unit != 0 else 1
                if unit != component.unit:
                    continue
                key = _symbol_endpoint_key(component.ref, unit, pin.number)
                point = ports.get(key)
                if point is None:
                    continue
                terminal = component.ports.get(key)
                items.append(
                    PlacedNoConnect(
                        at=point,
                        uuid=stable_uuid(f"{self.sheet_path}:{endpoint_text}:{index}:{pin.number}:no-connect"),
                        terminal=terminal.terminal if terminal is not None else None,
                    )
                )
        return tuple(items)

    def _lib_symbols(self) -> tuple[list[Any], ...]:
        definitions: list[list[Any]] = []
        for lib_id in sorted({symbol.lib for symbol in self.sheet.symbols.values()}):
            info = self.project.symbol_library.get(lib_id)
            if info is None or info.definition is None:
                continue
            definition = deepcopy(info.definition)
            definition[1] = info.lib_id
            definitions.append(definition)
        if self.sheet.power_flags:
            definitions.append(power_flag_symbol_definition())
        if any(_is_power_net(net_name) for net_name in self.net_records):
            definitions.append(power_port_symbol_definition())
            definitions.append(power_driver_symbol_definition())
        return tuple(definitions)


def _placed_symbol(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    unit: int,
    decl: SymbolDecl,
    symbol_info: SymbolInfo | None,
    at: Point,
    rotation: int,
    *,
    compact_value: bool = False,
) -> PlacedSymbol:
    value = _compact_display_value(decl.value or ref) if compact_value else (decl.value or ref)
    props = (
        compact_symbol_property_points(at.x, at.y, symbol_info, ref=ref, value=value, symbol_rotation=rotation)
        if compact_value
        else None
    )
    if props is None:
        props = symbol_property_points(at.x, at.y, symbol_info, ref=ref, symbol_rotation=rotation)
    property_rotation = 0 if rotation % 180 == 0 else (-rotation) % 360
    properties: list[PlacedProperty] = [
        PlacedProperty("Reference", ref, (props.reference.x, props.reference.y), justify=props.justify, rotation=property_rotation),
        PlacedProperty("Value", value, (props.value.x, props.value.y), justify=props.justify, rotation=property_rotation),
        PlacedProperty("Footprint", decl.footprint or "", (props.footprint.x, props.footprint.y), hidden=True, rotation=property_rotation),
    ]
    field_y = props.footprint.y
    for field_name, field_value in sorted(decl.fields.items()):
        if field_name in {"Reference", "Value", "Footprint"}:
            continue
        field_y = _snap(field_y + GRID)
        properties.append(PlacedProperty(field_name, field_value, (props.footprint.x, field_y), justify=props.justify, hidden=True, rotation=property_rotation))
    pins: list[PlacedSymbolPin] = []
    if symbol_info is not None:
        seen: set[str] = set()
        for pin in symbol_info.pins:
            if pin.number in seen:
                continue
            seen.add(pin.number)
            pins.append(PlacedSymbolPin(pin.number, stable_uuid(f"{sheet_path}:{ref}:{unit}:{pin.number}")))
    unit_suffix = "" if unit == 1 else f":unit:{unit}"
    return PlacedSymbol(
        lib_id=decl.lib,
        at=(at.x, at.y),
        unit=unit,
        uuid=stable_uuid(f"{sheet_path}:{ref}{unit_suffix}"),
        project_name=project.name,
        sheet_instance_path=sheet_instance_path(sheet_path),
        reference=ref,
        properties=tuple(properties),
        pins=tuple(pins),
        rotation=rotation,
    )


def _compact_display_value(value: str) -> str:
    tokens = value.split()
    if len(tokens) <= 1:
        return value
    if tokens[1] in {"0.1%", "1%", "2%", "5%", "10%", "6.3V", "10V", "16V", "25V", "50V", "60V", "100V"}:
        return " ".join(tokens[:2])
    return tokens[0]


def _component_at_for_port(
    component: Component,
    port: Port,
    target: tuple[float, float],
    rotation: int,
) -> tuple[float, float]:
    local = _rotated_point((port.local.x, port.local.y), rotation)
    return (_snap(target[0] - local[0]), _snap(target[1] - local[1]))


def _component_port_point(component: Component, port: Port, at: Point, rotation: int) -> tuple[float, float]:
    del component
    local = _rotated_point((port.local.x, port.local.y), rotation)
    return (_snap(at.x + local[0]), _snap(at.y + local[1]))


def _rotated_point(point: tuple[float, float], rotation: int) -> tuple[float, float]:
    x, y = point
    rotation = rotation % 360
    if rotation == 90:
        return (y, -x)
    if rotation == 180:
        return (-x, -y)
    if rotation == 270:
        return (-y, x)
    return (x, y)


def _rotated_side(side: PortSide, rotation: int) -> PortSide:
    sides: tuple[PortSide, ...] = ("NORTH", "EAST", "SOUTH", "WEST")
    index = sides.index(side)
    steps = (rotation % 360) // 90
    return sides[(index - steps) % len(sides)]


def _rotation_between_sides(source: PortSide, target: PortSide) -> int:
    sides: tuple[PortSide, ...] = ("NORTH", "EAST", "SOUTH", "WEST")
    return ((sides.index(source) - sides.index(target)) % len(sides)) * 90


def _opposite_side(side: PortSide) -> PortSide:
    return {
        "NORTH": "SOUTH",
        "SOUTH": "NORTH",
        "EAST": "WEST",
        "WEST": "EAST",
    }[side]


def _rail_lane_score(
    taps: list[tuple[tuple[float, float], tuple[float, float]]],
    trunk: tuple[tuple[float, float], tuple[float, float]],
    occupied: list[Rect],
) -> tuple[float, float]:
    overlap = 0.0
    length = 0.0
    occupied_index = _RectIndex(occupied)
    for start, end in [trunk, *taps]:
        length += abs(start[0] - end[0]) + abs(start[1] - end[1])
        rect = _inflate(_segment_rect(start, end), GRID / 4)
        overlap += _indexed_overlap_area(rect, occupied_index)
    for _start, end in taps:
        junction_rect = Rect(end[0] - 0.75, end[1] - 0.75, end[0] + 0.75, end[1] + 0.75)
        overlap += _indexed_overlap_area(junction_rect, occupied_index)
    return overlap, length


def _drop_overlapping_junctions(
    items: list[PlacedItem],
    symbol_library: dict[str, SymbolInfo],
) -> list[PlacedItem]:
    geometry = placed_items_geometry(tuple(items), symbol_library=symbol_library)
    bad_junction_ids: set[str] = set()
    for overlap in geometry.as_problem().overlaps():
        first, second = overlap.first, overlap.second
        if first.kind == "junction" and second.kind != "junction":
            bad_junction_ids.add(first.id)
        if second.kind == "junction" and first.kind != "junction":
            bad_junction_ids.add(second.id)
    if not bad_junction_ids:
        return items
    return [
        item
        for item in items
        if not (isinstance(item, PlacedJunction) and item.uuid in bad_junction_ids)
    ]


def _segment_endpoint_side(
    point: tuple[float, float],
    other: tuple[float, float],
) -> PortSide:
    dx = other[0] - point[0]
    dy = other[1] - point[1]
    if abs(dx) >= abs(dy):
        return "WEST" if dx > 0 else "EAST"
    return "NORTH" if dy > 0 else "SOUTH"


def _contiguous_rail_runs(
    records: list[NetEndpoint],
    side: PortSide,
    placed: dict[str, PlacedComponent],
) -> list[list[NetEndpoint]]:
    axis = 1 if side in {"WEST", "EAST"} else 0
    ordered = sorted(records, key=lambda record: placed[record.component_id].ports[record.endpoint_key][axis])
    runs: list[list[NetEndpoint]] = []
    current: list[NetEndpoint] = []
    previous_coord: float | None = None
    for record in ordered:
        coord = placed[record.component_id].ports[record.endpoint_key][axis]
        if previous_coord is not None and coord - previous_coord > GRID * 1.1:
            runs.append(current)
            current = []
        current.append(record)
        previous_coord = coord
    if current:
        runs.append(current)
    return runs


def _passive_power_rail_runs(
    records: list[NetEndpoint],
    side: PortSide,
    placed: dict[str, PlacedComponent],
) -> list[list[NetEndpoint]]:
    axis = 1 if side in {"WEST", "EAST"} else 0
    ordered = sorted(records, key=lambda record: placed[record.component_id].ports[record.endpoint_key][axis])
    runs: list[list[NetEndpoint]] = []
    current: list[NetEndpoint] = []
    previous_coord: float | None = None
    max_gap = SUPPORT_STEP * 4
    for record in ordered:
        coord = placed[record.component_id].ports[record.endpoint_key][axis]
        if previous_coord is not None and coord - previous_coord > max_gap:
            runs.append(current)
            current = []
        current.append(record)
        previous_coord = coord
    if current:
        runs.append(current)
    return runs


def _wire_items(
    sheet_path: str,
    net_name: str,
    start: tuple[float, float],
    end: tuple[float, float],
    start_terminal: str | None,
    end_terminal: str | None,
    key: str,
) -> list[PlacedItem]:
    points = _orthogonal_points(start, end)
    return _wire_items_from_points(sheet_path, net_name, points, start_terminal, end_terminal, key)


def _wire_items_avoiding(
    sheet_path: str,
    net_name: str,
    start: tuple[float, float],
    end: tuple[float, float],
    start_terminal: str | None,
    end_terminal: str | None,
    key: str,
    avoid_elements: list[LayoutElement],
    existing_items: list[PlacedItem] | None = None,
    *,
    start_side: PortSide | None = None,
    end_side: PortSide | None = None,
) -> list[PlacedItem]:
    path_context = _path_context(avoid_elements, _existing_wire_segments(existing_items or []))
    choice = _best_orthogonal_path(
        start,
        end,
        net_name=net_name,
        start_terminal=start_terminal,
        end_terminal=end_terminal,
        start_side=start_side,
        end_side=end_side,
        path_context=path_context,
    )
    return _wire_items_from_points(sheet_path, net_name, list(choice.points), start_terminal, end_terminal, key)


def _wire_batch_avoiding(
    sheet_path: str,
    requests: list[_WireRequest],
    avoid_elements: list[LayoutElement],
    existing_items: list[PlacedItem],
) -> list[PlacedItem]:
    if not requests:
        return []
    existing_segments = _existing_wire_segments(existing_items)
    order = sorted(range(len(requests)), key=lambda index: _wire_request_route_order(requests[index]))
    routed_segments: list[tuple[frozenset[str], tuple[float, float, float, float]]] = []
    by_request: dict[int, _RouteCandidate] = {}
    for request_index in order:
        path_context = _path_context(avoid_elements, [*existing_segments, *routed_segments])
        candidates = _route_candidates_for_request(request_index, requests[request_index], path_context)
        if not candidates:
            continue
        candidate = candidates[0]
        by_request[request_index] = candidate
        routed_segments.extend(candidate.segments)
    items: list[PlacedItem] = []
    for index, request in enumerate(requests):
        candidate = by_request.get(index)
        if candidate is None:
            continue
        items.extend(
            _wire_items_from_points(
                sheet_path,
                request.net_name,
                list(candidate.points),
                request.start_terminal,
                request.end_terminal,
                request.key,
            )
        )
    return items


def _provisional_wire_score(
    prior_requests: tuple[_WireRequest, ...],
    request: _WireRequest,
    *,
    existing_segments: tuple[tuple[frozenset[str], tuple[float, float, float, float]], ...] = (),
) -> float:
    points = _orthogonal_points(request.start, request.end)
    length = 0.0
    for first, second in zip(points, points[1:]):
        length += abs(first[0] - second[0]) + abs(first[1] - second[1])
    bends = max(0, len(points) - 2)
    return (
        length
        + bends * 100.0
        + _provisional_wire_contact_penalty(prior_requests, request)
        + _provisional_existing_contact_penalty(existing_segments, request)
    )


def _provisional_existing_contact_penalty(
    existing_segments: tuple[tuple[frozenset[str], tuple[float, float, float, float]], ...],
    request: _WireRequest,
) -> float:
    if not existing_segments:
        return 0.0
    contacts = 0
    for segment in _provisional_request_segments(request):
        for nets, existing in existing_segments:
            if request.net_name in nets:
                continue
            if segments_touch(segment, existing):
                contacts += 1
    return contacts * ROUTE_CONTACT_WEIGHT


def _provisional_wire_contact_penalty(
    prior_requests: tuple[_WireRequest, ...],
    request: _WireRequest,
) -> float:
    if not prior_requests:
        return 0.0
    request_segments = _provisional_request_segments(request)
    contacts = 0
    for prior in prior_requests:
        if prior.net_name == request.net_name:
            continue
        for first in request_segments:
            for second in _provisional_request_segments(prior):
                if segments_touch(first, second):
                    contacts += 1
    return contacts * ROUTE_CONTACT_WEIGHT


def _provisional_request_segments(
    request: _WireRequest,
) -> tuple[tuple[float, float, float, float], ...]:
    points = _orthogonal_points(request.start, request.end)
    return tuple(
        (first[0], first[1], second[0], second[1])
        for first, second in zip(points, points[1:])
        if not _same_point(first, second)
    )


def _route_candidates_for_request(
    request_index: int,
    request: _WireRequest,
    path_context: _PathContext,
) -> list[_RouteCandidate]:
    candidates: list[_RouteCandidate] = []
    seen: set[tuple[tuple[float, float], ...]] = set()
    clean_found = False

    def add_options(options: list[list[tuple[float, float]]]) -> None:
        nonlocal clean_found
        for points in options:
            key = tuple(points)
            if key in seen:
                continue
            seen.add(key)
            choice = _path_choice(
                points,
                path_context,
                net_name=request.net_name,
                start_terminal=request.start_terminal,
                end_terminal=request.end_terminal,
                start_side=request.start_side,
                end_side=request.end_side,
            )
            clean_found = clean_found or (choice.blockers == 0 and choice.contacts == 0)
            candidates.append(
                _RouteCandidate(
                    request_index=request_index,
                    points=key,
                    score=choice.score,
                    segments=_candidate_segments(request.net_name, points),
                )
            )

    add_options(
        _orthogonal_path_options(
            request.start,
            request.end,
            start_side=request.start_side,
            end_side=request.end_side,
        )
    )
    if not clean_found:
        add_options(_obstacle_detour_path_options(request, path_context))
    return sorted(candidates, key=lambda candidate: candidate.score)[:ROUTE_CANDIDATE_LIMIT]


def _obstacle_detour_path_options(
    request: _WireRequest,
    path_context: _PathContext,
) -> list[list[tuple[float, float]]]:
    blockers, contact_segments = _request_path_obstacles(request, path_context)
    detour_xs: set[float] = set()
    detour_ys: set[float] = set()
    margin = GRID * 2
    for rect in blockers:
        detour_xs.add(_snap(rect.left - margin))
        detour_xs.add(_snap(rect.right + margin))
        detour_ys.add(_snap(rect.top - margin))
        detour_ys.add(_snap(rect.bottom + margin))
    for segment in contact_segments:
        rect = _segment_tuple_rect(segment)
        detour_xs.add(_snap(rect.left - margin))
        detour_xs.add(_snap(rect.right + margin))
        detour_ys.add(_snap(rect.top - margin))
        detour_ys.add(_snap(rect.bottom + margin))

    if not detour_xs and not detour_ys:
        return []

    start_escape = _escape_point(request.start, request.start_side, GRID * 2) if request.start_side is not None else request.start
    end_escape = _escape_point(request.end, request.end_side, GRID * 2) if request.end_side is not None else request.end
    min_x = min(request.start[0], request.end[0]) - SUPPORT_STEP * 8
    max_x = max(request.start[0], request.end[0]) + SUPPORT_STEP * 8
    min_y = min(request.start[1], request.end[1]) - SUPPORT_STEP * 8
    max_y = max(request.start[1], request.end[1]) + SUPPORT_STEP * 8

    options: list[list[tuple[float, float]]] = []
    for x in sorted(detour_xs):
        if min_x <= x <= max_x:
            options.append(_dedupe_path([request.start, start_escape, (x, start_escape[1]), (x, end_escape[1]), end_escape, request.end]))
    for y in sorted(detour_ys):
        if min_y <= y <= max_y:
            options.append(_dedupe_path([request.start, start_escape, (start_escape[0], y), (end_escape[0], y), end_escape, request.end]))
    return options


def _request_path_obstacles(
    request: _WireRequest,
    path_context: _PathContext,
) -> tuple[list[Rect], list[tuple[float, float, float, float]]]:
    blocker_rects: list[Rect] = []
    contact_segments: list[tuple[float, float, float, float]] = []
    seen_blockers: set[str] = set()
    seen_segments: set[tuple[float, float, float, float]] = set()
    for points in _orthogonal_path_options(
        request.start,
        request.end,
        start_side=request.start_side,
        end_side=request.end_side,
    )[:12]:
        last_index = len(points) - 2
        for index, (first, second) in enumerate(zip(points, points[1:])):
            segment = (first[0], first[1], second[0], second[1])
            candidate = LayoutSegment(
                id="candidate",
                owner=request.net_name,
                kind="wire",
                start=Point(first[0], first[1]),
                end=Point(second[0], second[1]),
                nets=frozenset({request.net_name}),
                start_terminals=frozenset({request.start_terminal}) if index == 0 and request.start_terminal else frozenset(),
                end_terminals=frozenset({request.end_terminal}) if index == last_index and request.end_terminal else frozenset(),
            )
            for element in path_context.avoid_index.query_segment(segment):
                if segment_blocked_by_element(candidate, element) and element.id not in seen_blockers:
                    seen_blockers.add(element.id)
                    blocker_rects.append(element.rect)
            for nets, existing in path_context.existing_index.query_segment(segment):
                if request.net_name in nets:
                    continue
                normalized = _normalized_segment(existing)
                if normalized not in seen_segments and segments_touch(segment, existing):
                    seen_segments.add(normalized)
                    contact_segments.append(existing)
    return blocker_rects[:24], contact_segments[:24]


def _wire_request_route_order(request: _WireRequest) -> tuple[int, float, int, float, float, str]:
    aligned = 0 if _request_is_aligned(request) else 1
    length = _manhattan(request.start, request.end)
    side_order = {"WEST": 0, "EAST": 1, "NORTH": 2, "SOUTH": 3}
    side = request.start_side
    if side in {"WEST", "EAST"}:
        return (aligned, length, side_order[side], request.start[1], request.start[0], request.key)
    if side in {"NORTH", "SOUTH"}:
        return (aligned, length, side_order[side], request.start[0], request.start[1], request.key)
    return (aligned, length, 9, request.start[1], request.start[0], request.key)


def _request_is_aligned(request: _WireRequest) -> bool:
    return abs(request.start[0] - request.end[0]) < 0.001 or abs(request.start[1] - request.end[1]) < 0.001


def _candidate_segments(
    net_name: str,
    points: list[tuple[float, float]],
) -> tuple[tuple[frozenset[str], tuple[float, float, float, float]], ...]:
    return tuple(
        (
            frozenset({net_name}),
            (first[0], first[1], second[0], second[1]),
        )
        for first, second in zip(points, points[1:])
        if not _same_point(first, second)
    )


def _normalized_segment(segment: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = segment
    first = (_snap(x1), _snap(y1))
    second = (_snap(x2), _snap(y2))
    if second < first:
        first, second = second, first
    return (first[0], first[1], second[0], second[1])


def _wire_items_from_points(
    sheet_path: str,
    net_name: str,
    points: list[tuple[float, float]],
    start_terminal: str | None,
    end_terminal: str | None,
    key: str,
) -> list[PlacedItem]:
    items: list[PlacedItem] = []
    for index, (first, second) in enumerate(zip(points, points[1:])):
        if _same_point(first, second):
            continue
        items.append(
            PlacedWire(
                start=first,
                end=second,
                uuid=stable_uuid(f"{sheet_path}:{key}:wire:{index}"),
                nets=frozenset({net_name}),
                start_terminals=frozenset({start_terminal}) if index == 0 and start_terminal else frozenset(),
                end_terminals=frozenset({end_terminal}) if index == len(points) - 2 and end_terminal else frozenset(),
            )
        )
    return items


def _rail_wire_items(
    sheet_path: str,
    net_name: str,
    points: list[tuple[float, float]],
    taps: set[tuple[float, float]],
    key: str,
) -> list[PlacedItem]:
    snapped_points = [(_snap(point[0]), _snap(point[1])) for point in points]
    unique_points = list(dict.fromkeys(snapped_points))
    if len(unique_points) < 2:
        return []
    xs = {point[0] for point in unique_points}
    ys = {point[1] for point in unique_points}
    if len(ys) == 1:
        ordered_points = sorted(unique_points, key=lambda point: point[0])
    elif len(xs) == 1:
        ordered_points = sorted(unique_points, key=lambda point: point[1])
    else:
        return _wire_items_from_points(sheet_path, net_name, unique_points, None, None, key)

    items: list[PlacedItem] = []
    for index, (first, second) in enumerate(zip(ordered_points, ordered_points[1:])):
        if _same_point(first, second):
            continue
        items.append(
            PlacedWire(
                start=first,
                end=second,
                uuid=stable_uuid(f"{sheet_path}:{key}:rail-wire:{index}"),
                nets=frozenset({net_name}),
            )
        )

    snapped_taps = {(_snap(point[0]), _snap(point[1])) for point in taps}
    for index, point in enumerate(sorted(snapped_taps)):
        items.append(
            PlacedJunction(
                at=point,
                uuid=stable_uuid(f"{sheet_path}:{key}:junction:{index}:{point[0]}:{point[1]}"),
                nets=frozenset({net_name}),
            )
        )
    return items


def _best_orthogonal_path(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    net_name: str,
    start_terminal: str | None,
    end_terminal: str | None,
    start_side: PortSide | None,
    end_side: PortSide | None,
    path_context: _PathContext,
) -> _PathChoice:
    options = _orthogonal_path_options(
        start,
        end,
        start_side=start_side,
        end_side=end_side,
    )
    scored_options = sorted(
        (
            _path_base_score(points, start_side=start_side, end_side=end_side),
            points,
        )
        for points in options
    )
    best: _PathChoice | None = None
    for base_score, points in scored_options:
        if best is not None and base_score >= best.score:
            break
        choice = _path_choice(
            points,
            path_context,
            net_name=net_name,
            start_terminal=start_terminal,
            end_terminal=end_terminal,
            start_side=start_side,
            end_side=end_side,
            base_score=base_score,
        )
        if best is None or choice.score < best.score:
            best = choice
    assert best is not None
    return best


def _path_choice(
    points: list[tuple[float, float]],
    path_context: _PathContext,
    *,
    net_name: str,
    start_terminal: str | None,
    end_terminal: str | None,
    start_side: PortSide | None,
    end_side: PortSide | None,
    base_score: float | None = None,
) -> _PathChoice:
    blockers, contacts = _path_issue_presence(
        points,
        path_context,
        net_name=net_name,
        start_terminal=start_terminal,
        end_terminal=end_terminal,
    )
    score = (
        blockers * ROUTE_BLOCKER_WEIGHT
        + contacts * ROUTE_CONTACT_WEIGHT
        + (base_score if base_score is not None else _path_base_score(points, start_side=start_side, end_side=end_side))
    )
    return _PathChoice(tuple(points), score, blockers, contacts)


def _path_base_score(
    points: list[tuple[float, float]],
    *,
    start_side: PortSide | None,
    end_side: PortSide | None,
) -> float:
    length = 0.0
    for first, second in zip(points, points[1:]):
        length += abs(first[0] - second[0]) + abs(first[1] - second[1])
    bends = max(0, len(points) - 2)
    return _port_escape_penalty(points, start_side=start_side, end_side=end_side) + bends * 100.0 + length


def _orthogonal_path_options(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    start_side: PortSide | None = None,
    end_side: PortSide | None = None,
) -> list[list[tuple[float, float]]]:
    min_x = min(start[0], end[0])
    max_x = max(start[0], end[0])
    min_y = min(start[1], end[1])
    max_y = max(start[1], end[1])
    options: list[list[tuple[float, float]]] = [
        _orthogonal_points(start, end),
        [start, (_snap(end[0]), start[1]), end],
        [start, (start[0], _snap(end[1])), end],
    ]
    route_offsets = (GRID, GRID * 2, GRID * 3, GRID * 4, SUPPORT_STEP, SUPPORT_STEP * 2, SUPPORT_STEP * 3, SUPPORT_STEP * 4)
    for offset in route_offsets:
        left = _snap(min_x - offset)
        right = _snap(max_x + offset)
        top = _snap(min_y - offset)
        bottom = _snap(max_y + offset)
        options.extend(
            [
                [start, (left, start[1]), (left, end[1]), end],
                [start, (right, start[1]), (right, end[1]), end],
                [start, (start[0], top), (end[0], top), end],
                [start, (start[0], bottom), (end[0], bottom), end],
            ]
        )
    if start_side is not None or end_side is not None:
        for offset in route_offsets:
            start_escape = _escape_point(start, start_side, offset) if start_side is not None else start
            end_escape = _escape_point(end, end_side, offset) if end_side is not None else end
            options.extend(
                [
                    [start, start_escape, (_snap(start_escape[0]), _snap(end_escape[1])), end_escape, end],
                    [start, start_escape, (_snap(end_escape[0]), _snap(start_escape[1])), end_escape, end],
                ]
            )
            escaped_min_x = min(start_escape[0], end_escape[0])
            escaped_max_x = max(start_escape[0], end_escape[0])
            escaped_min_y = min(start_escape[1], end_escape[1])
            escaped_max_y = max(start_escape[1], end_escape[1])
            for detour in route_offsets:
                left = _snap(escaped_min_x - detour)
                right = _snap(escaped_max_x + detour)
                top = _snap(escaped_min_y - detour)
                bottom = _snap(escaped_max_y + detour)
                options.extend(
                    [
                        [start, start_escape, (left, start_escape[1]), (left, end_escape[1]), end_escape, end],
                        [start, start_escape, (right, start_escape[1]), (right, end_escape[1]), end_escape, end],
                        [start, start_escape, (start_escape[0], top), (end_escape[0], top), end_escape, end],
                        [start, start_escape, (start_escape[0], bottom), (end_escape[0], bottom), end_escape, end],
                    ]
                )
    deduped = [_dedupe_path(points) for points in options]
    if start_side is not None or end_side is not None:
        outward = [
            points
            for points in deduped
            if _port_escape_penalty(points, start_side=start_side, end_side=end_side) == 0.0
        ]
        if outward:
            return outward
    return deduped


def _dedupe_path(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        snapped = (_snap(point[0]), _snap(point[1]))
        if deduped and _same_point(deduped[-1], snapped):
            continue
        deduped.append(snapped)
    return deduped


def _escape_point(
    point: tuple[float, float],
    side: PortSide,
    offset: float,
) -> tuple[float, float]:
    vector = _side_vector(side)
    return (_snap(point[0] + vector[0] * offset), _snap(point[1] + vector[1] * offset))


def _port_escape_penalty(
    points: list[tuple[float, float]],
    *,
    start_side: PortSide | None,
    end_side: PortSide | None,
) -> float:
    penalty = 0.0
    if start_side is not None and len(points) >= 2:
        penalty += _side_escape_penalty(points[0], points[1], start_side)
    if end_side is not None and len(points) >= 2:
        penalty += _side_escape_penalty(points[-1], points[-2], end_side)
    return penalty


def _side_escape_penalty(
    anchor: tuple[float, float],
    peer: tuple[float, float],
    side: PortSide,
) -> float:
    dx = peer[0] - anchor[0]
    dy = peer[1] - anchor[1]
    if abs(dx) < 0.001 and abs(dy) < 0.001:
        return PORT_ESCAPE_WEIGHT
    if side == "WEST":
        return 0.0 if dx < -0.001 and abs(dy) < 0.001 else PORT_ESCAPE_WEIGHT
    if side == "EAST":
        return 0.0 if dx > 0.001 and abs(dy) < 0.001 else PORT_ESCAPE_WEIGHT
    if side == "NORTH":
        return 0.0 if dy < -0.001 and abs(dx) < 0.001 else PORT_ESCAPE_WEIGHT
    return 0.0 if dy > 0.001 and abs(dx) < 0.001 else PORT_ESCAPE_WEIGHT


def _power_port_symbol_rotation(side: PortSide) -> int:
    return {
        "NORTH": 0,
        "EAST": 90,
        "SOUTH": 180,
        "WEST": 270,
    }[side]


def _path_issue_counts(
    points: list[tuple[float, float]],
    path_context: _PathContext,
    *,
    net_name: str,
    start_terminal: str | None,
    end_terminal: str | None,
) -> tuple[int, int]:
    blockers = 0
    contacts = 0
    last_index = len(points) - 2
    for index, (first, second) in enumerate(zip(points, points[1:])):
        segment = (first[0], first[1], second[0], second[1])
        candidate = LayoutSegment(
            id="candidate",
            owner=net_name,
            kind="wire",
            start=Point(first[0], first[1]),
            end=Point(second[0], second[1]),
            nets=frozenset({net_name}),
            start_terminals=frozenset({start_terminal}) if index == 0 and start_terminal else frozenset(),
            end_terminals=frozenset({end_terminal}) if index == last_index and end_terminal else frozenset(),
        )
        blockers += sum(
            1
            for element in path_context.avoid_index.query_segment(segment)
            if segment_blocked_by_element(candidate, element)
        )
        contacts += sum(
            1
            for nets, existing in path_context.existing_index.query_segment(segment)
            if net_name not in nets and segments_touch(segment, existing)
        )
    return blockers, contacts


def _path_issue_presence(
    points: list[tuple[float, float]],
    path_context: _PathContext,
    *,
    net_name: str,
    start_terminal: str | None,
    end_terminal: str | None,
) -> tuple[int, int]:
    last_index = len(points) - 2
    segments: list[tuple[float, float, float, float]] = []
    for index, (first, second) in enumerate(zip(points, points[1:])):
        segment = (first[0], first[1], second[0], second[1])
        segments.append(segment)
        candidate = LayoutSegment(
            id="candidate",
            owner=net_name,
            kind="wire",
            start=Point(first[0], first[1]),
            end=Point(second[0], second[1]),
            nets=frozenset({net_name}),
            start_terminals=frozenset({start_terminal}) if index == 0 and start_terminal else frozenset(),
            end_terminals=frozenset({end_terminal}) if index == last_index and end_terminal else frozenset(),
        )
        if any(segment_blocked_by_element(candidate, element) for element in path_context.avoid_index.query_segment(segment)):
            return 1, 0
    for segment in segments:
        if any(
            net_name not in nets and segments_touch(segment, existing)
            for nets, existing in path_context.existing_index.query_segment(segment)
        ):
            return 0, 1
    return 0, 0


def _stub_path_issue_penalty(
    points: list[tuple[float, float]],
    path_context: _PathContext,
    *,
    net_name: str,
    start_terminal: str | None,
    end_terminal: str | None,
) -> float:
    last_index = len(points) - 2
    segments: list[tuple[tuple[float, float, float, float], LayoutSegment]] = []
    for index, (first, second) in enumerate(zip(points, points[1:])):
        segment = (first[0], first[1], second[0], second[1])
        candidate = LayoutSegment(
            id="candidate",
            owner=net_name,
            kind="wire",
            start=Point(first[0], first[1]),
            end=Point(second[0], second[1]),
            nets=frozenset({net_name}),
            start_terminals=frozenset({start_terminal}) if index == 0 and start_terminal else frozenset(),
            end_terminals=frozenset({end_terminal}) if index == last_index and end_terminal else frozenset(),
        )
        segments.append((segment, candidate))
        if any(segment_blocked_by_element(candidate, element) for element in path_context.avoid_index.query_segment(segment)):
            return 20_000_000_000.0
    for segment, _candidate in segments:
        if any(
            net_name not in nets and segments_touch(segment, existing)
            for nets, existing in path_context.existing_index.query_segment(segment)
        ):
            return 10_000_000_000.0
    return 0.0


def _existing_wire_segments(
    items: list[PlacedItem],
) -> list[tuple[frozenset[str], tuple[float, float, float, float]]]:
    return [
        (item.nets, (item.start[0], item.start[1], item.end[0], item.end[1]))
        for item in items
        if isinstance(item, PlacedWire) and item.nets
    ]


def _power_port_items(
    project: ResolvedProject,
    sheet_path: str,
    net_name: str,
    label_text: str,
    point: tuple[float, float],
    side: PortSide,
    occupied: list[Rect],
    endpoint_key: str,
    *,
    axis_locked: bool = False,
    hard_occupied: list[Rect] | None = None,
    driven: bool = False,
    existing_items: list[PlacedItem] | None = None,
    symbol_library: dict[str, SymbolInfo] | None = None,
) -> list[PlacedItem]:
    symbol_point, value_at, justify, rect = _power_port_anchor(
        label_text,
        point,
        side,
        occupied,
        axis_locked=axis_locked,
        hard_occupied=hard_occupied,
        route_net_name=net_name,
        existing_items=existing_items,
        symbol_library=symbol_library,
    )
    occupied.append(_inflate(rect, GRID / 2))
    key = f"{endpoint_key}:{net_name}:power-port"
    items: list[PlacedItem] = []
    if not _same_point(point, symbol_point):
        wire_items = _wire_items(
            sheet_path,
            net_name,
            point,
            symbol_point,
            None,
            None,
            key + ":stub",
        )
        items.extend(wire_items)
        occupied.extend(_wire_avoid_rects(wire_items))
    hidden_value = label_text != net_name
    items.append(
        power_port_symbol(
            sheet_path,
            net_name,
            endpoint_key,
            Point(symbol_point[0], symbol_point[1]),
            Point(value_at[0], value_at[1]),
            value=net_name if hidden_value else label_text,
            justify=justify,
            rotation=(-_power_port_symbol_rotation(side)) % 360,
            symbol_rotation=_power_port_symbol_rotation(side),
            hidden_value=hidden_value,
            project_name=project.name,
            sheet_instance_path=sheet_instance_path(sheet_path),
        ),
    )
    if hidden_value:
        items.append(
            PlacedText(
                text=label_text,
                at=value_at,
                uuid=stable_uuid(f"{sheet_path}:{endpoint_key}:{net_name}:power-display-text"),
                justify=justify,
            )
        )
    if driven:
        items.append(
            power_driver_symbol(
                sheet_path,
                net_name,
                endpoint_key,
                Point(symbol_point[0], symbol_point[1]),
                project_name=project.name,
                sheet_instance_path=sheet_instance_path(sheet_path),
            )
        )
    return items


def _power_port_anchor(
    label_text: str,
    point: tuple[float, float],
    side: PortSide,
    occupied: list[Rect],
    *,
    axis_locked: bool = False,
    hard_occupied: list[Rect] | None = None,
    route_net_name: str | None = None,
    existing_items: list[PlacedItem] | None = None,
    symbol_library: dict[str, SymbolInfo] | None = None,
) -> tuple[tuple[float, float], tuple[float, float], Literal["left", "right"], Rect]:
    options: list[
        tuple[
            float,
            tuple[float, float],
            tuple[float, float],
            Literal["left", "right"],
            Rect,
        ]
    ] = []
    hard = hard_occupied if hard_occupied is not None else occupied
    path_context = (
        _path_context(
            _route_avoid_elements(existing_items, symbol_library),
            _existing_wire_segments(existing_items),
        )
        if existing_items is not None and symbol_library is not None and route_net_name is not None
        else None
    )
    lanes = _lane_offsets(limit=4) if axis_locked else _lane_offsets(limit=2)
    sides = (side, _opposite_side(side)) if axis_locked else (side, _opposite_side(side))
    occupied_index = _RectIndex(occupied)
    hard_index = _RectIndex(hard)
    best_score = float("inf")
    for candidate_side in sides:
        side_penalty = 0.0 if candidate_side == side else 50_000.0
        offset_count = 48 if axis_locked else 12
        for offset in (index * GRID for index in range(offset_count)):
            for lane in lanes:
                if candidate_side == "WEST":
                    symbol_point = (_snap(point[0] - offset), _snap(point[1] + lane))
                elif candidate_side == "EAST":
                    symbol_point = (_snap(point[0] + offset), _snap(point[1] + lane))
                elif candidate_side == "NORTH":
                    symbol_point = (_snap(point[0] + lane), _snap(point[1] - offset))
                else:
                    symbol_point = (_snap(point[0] + lane), _snap(point[1] + offset))
                value_at, justify = _power_port_value_position(label_text, symbol_point, candidate_side)
                rect = text_rect(Point(value_at[0], value_at[1]), label_text, justify=justify)
                hard_overlap = _indexed_overlap_area(rect, hard_index)
                overlap = _indexed_overlap_area(rect, occupied_index)
                axis_penalty = abs(lane) * (1000.0 if axis_locked else 1.0)
                distance = offset + abs(lane)
                cheap_score = (
                    hard_overlap * 1_000_000_000.0
                    + overlap * 1_000_000_000.0
                    + side_penalty
                    + axis_penalty
                    + distance
                )
                if cheap_score >= best_score:
                    continue
                route_penalty = _power_port_stub_penalty(
                    point,
                    symbol_point,
                    route_net_name,
                    path_context,
                )
                score = cheap_score + route_penalty
                best_score = min(best_score, score)
                options.append(
                    (
                        score,
                        symbol_point,
                        value_at,
                        justify,
                        rect,
                    )
                )
    _score, symbol_point, value_at, justify, rect = min(options, key=lambda item: item[0])
    return symbol_point, value_at, justify, rect


def _power_port_stub_penalty(
    point: tuple[float, float],
    symbol_point: tuple[float, float],
    net_name: str | None,
    path_context: _PathContext | None,
) -> float:
    if net_name is None or path_context is None or _same_point(point, symbol_point):
        return 0.0
    return _stub_path_issue_penalty(
        _orthogonal_points(point, symbol_point),
        path_context,
        net_name=net_name,
        start_terminal=None,
        end_terminal=None,
    )


def _power_port_value_position(
    net_name: str,
    symbol_point: tuple[float, float],
    side: PortSide,
) -> tuple[tuple[float, float], Literal["left", "right"]]:
    x, y = symbol_point
    if side == "WEST":
        return (_snap(x - LABEL_GAP), y), "right"
    if side == "EAST":
        return (_snap(x + LABEL_GAP), y), "left"
    if side == "NORTH":
        return (_snap(x + LABEL_GAP), _snap(y - LABEL_GAP)), "left"
    return (_snap(x + LABEL_GAP), _snap(y + LABEL_GAP)), "left"


def _label_items(
    sheet_path: str,
    net_name: str,
    label_text: str,
    point: tuple[float, float],
    side: PortSide,
    kind: Literal["local", "hierarchical"],
    occupied: list[Rect],
    terminal: str | None,
    endpoint_key: str,
    *,
    axis_locked: bool = False,
    existing_items: list[PlacedItem] | None = None,
    symbol_library: dict[str, SymbolInfo] | None = None,
    path_context: _PathContext | None = None,
) -> list[PlacedItem]:
    key = f"{sheet_path}:{endpoint_key}:{net_name}:label"
    if sheet_path == "/" and kind == "local":
        anchor = (_snap(point[0]), _snap(point[1]))
        justify: Literal["left", "right"] = "right" if side == "WEST" else "left"
        rect = text_rect(Point(anchor[0], anchor[1]), label_text, justify=justify)
        items: list[PlacedItem] = []
    elif existing_items is not None and symbol_library is not None:
        anchor, justify, rect, points = _label_anchor_and_route(
            label_text,
            point,
            side,
            occupied,
            net_name=net_name,
            terminal=terminal,
            axis_locked=axis_locked,
            existing_items=existing_items,
            symbol_library=symbol_library,
            path_context=path_context,
        )
        items: list[PlacedItem] = _wire_items_from_points(sheet_path, net_name, points, terminal, None, key + ":stub")
    else:
        anchor, justify, rect = _label_anchor(label_text, point, side, occupied, axis_locked=axis_locked)
        items = _wire_items(sheet_path, net_name, point, anchor, terminal, None, key + ":stub")
    occupied.append(_inflate(rect, GRID / 2))
    occupied.extend(_wire_avoid_rects(items))
    if kind == "hierarchical":
        items.append(
            PlacedHierarchicalLabel(
                name=label_text,
                shape="bidirectional",
                at=anchor,
                uuid=stable_uuid(key),
                justify=justify,
                rotation=_label_rotation(justify),
            )
        )
    else:
        items.append(
            PlacedLabel(
                name=label_text,
                at=anchor,
                uuid=stable_uuid(key),
                justify=justify,
                rotation=_label_rotation(justify),
                nets=frozenset({net_name}),
            )
        )
    return items


def _label_rotation(justify: Literal["left", "right"]) -> int:
    del justify
    return 0


def _label_anchor(
    net_name: str,
    point: tuple[float, float],
    side: PortSide,
    occupied: list[Rect],
    *,
    axis_locked: bool = False,
) -> tuple[tuple[float, float], Literal["left", "right"], Rect]:
    options = _label_anchor_candidates(
        net_name,
        point,
        side,
        occupied,
        axis_locked=axis_locked,
        candidate_sides=_default_label_sides(side, axis_locked=axis_locked),
        path_context=None,
        route_net_name=None,
        terminal=None,
    )
    best = min(options, key=lambda item: item.score)
    return best.anchor, best.justify, best.rect


def _label_anchor_and_route(
    label_text: str,
    point: tuple[float, float],
    side: PortSide,
    occupied: list[Rect],
    *,
    net_name: str,
    terminal: str | None,
    axis_locked: bool,
    existing_items: list[PlacedItem],
    symbol_library: dict[str, SymbolInfo],
    path_context: _PathContext | None = None,
) -> tuple[tuple[float, float], Literal["left", "right"], Rect, list[tuple[float, float]]]:
    if path_context is None:
        path_context = _path_context(
            _route_avoid_elements(existing_items, symbol_library),
            _existing_wire_segments(existing_items),
        )
    candidate = min(
        _label_anchor_candidates(
            label_text,
            point,
            side,
            occupied,
            axis_locked=axis_locked,
            candidate_sides=_default_label_sides(side, axis_locked=axis_locked),
            path_context=path_context,
            route_net_name=net_name,
            terminal=terminal,
        ),
        key=lambda item: item.score,
    )
    if candidate.score >= ROUTE_BLOCKER_WEIGHT:
        candidate = min(
            _label_anchor_candidates(
                label_text,
                point,
                side,
                occupied,
                axis_locked=axis_locked,
                candidate_sides=_label_candidate_sides(side, axis_locked=axis_locked),
                path_context=path_context,
                route_net_name=net_name,
                terminal=terminal,
            ),
            key=lambda item: item.score,
        )
    return candidate.anchor, candidate.justify, candidate.rect, _orthogonal_points(point, candidate.anchor)


def _label_route_overlap(
    point: tuple[float, float],
    anchor: tuple[float, float],
    occupied_index: _RectIndex,
) -> float:
    points = _orthogonal_points(point, anchor)
    overlap = 0.0
    for first, second in zip(points, points[1:]):
        stub_rect = _inflate(_segment_rect(first, second), GRID / 4)
        overlap += _indexed_overlap_area(stub_rect, occupied_index)
    return overlap


def _label_anchor_candidates(
    net_name: str,
    point: tuple[float, float],
    side: PortSide,
    occupied: list[Rect],
    *,
    axis_locked: bool = False,
    candidate_sides: tuple[PortSide, ...] | None = None,
    path_context: _PathContext | None = None,
    route_net_name: str | None = None,
    terminal: str | None = None,
) -> list[_LabelAnchorCandidate]:
    options: list[_LabelAnchorCandidate] = []
    occupied_index = _RectIndex(occupied)
    lanes = _lane_offsets(limit=4) if axis_locked and side in {"NORTH", "SOUTH"} else ((0.0,) if axis_locked else _lane_offsets(limit=4))
    offset_count = 24 if axis_locked else 8
    sides = candidate_sides if candidate_sides is not None else _default_label_sides(side, axis_locked=axis_locked)
    best_score = float("inf")
    for candidate_side in sides:
        side_penalty = _label_side_penalty(side, candidate_side)
        for offset in (LABEL_GAP + index * GRID for index in range(offset_count)):
            for lane in lanes:
                if candidate_side == "WEST":
                    anchor = (_snap(point[0] - offset), _snap(point[1] + lane))
                    justify: Literal["left", "right"] = "right"
                elif candidate_side == "EAST":
                    anchor = (_snap(point[0] + offset), _snap(point[1] + lane))
                    justify = "left"
                elif candidate_side == "NORTH":
                    anchor = (_snap(point[0] + lane), _snap(point[1] - offset))
                    justify = "left" if lane >= 0 else "right"
                else:
                    anchor = (_snap(point[0] + lane), _snap(point[1] + offset))
                    justify = "left" if lane >= 0 else "right"
                rect = text_rect(Point(anchor[0], anchor[1]), net_name, justify=justify)
                overlap = _indexed_overlap_area(rect, occupied_index)
                distance = abs(lane) + offset
                text_score = (
                    overlap * 1_000_000.0
                    + side_penalty
                    + distance
                )
                if text_score >= best_score:
                    continue
                route_overlap = _label_route_overlap(point, anchor, occupied_index)
                self_overlap = _path_rect_overlap_area(point, anchor, (rect,))
                cheap_score = text_score + route_overlap * 2_000_000.0 + self_overlap * 200_000.0
                if cheap_score >= best_score:
                    continue
                route_penalty = _label_stub_path_penalty(
                    point,
                    anchor,
                    route_net_name,
                    terminal,
                    candidate_side,
                    path_context,
                )
                score = route_penalty + cheap_score
                best_score = min(best_score, score)
                options.append(
                    _LabelAnchorCandidate(
                        anchor,
                        justify,
                        rect,
                        candidate_side,
                        score,
                        route_penalty + overlap * 1_000_000.0 + side_penalty + distance,
                    )
                )
    return options


def _label_stub_path_penalty(
    point: tuple[float, float],
    anchor: tuple[float, float],
    net_name: str | None,
    terminal: str | None,
    side: PortSide,
    path_context: _PathContext | None,
) -> float:
    del side
    if net_name is None or path_context is None or _same_point(point, anchor):
        return 0.0
    return _stub_path_issue_penalty(
        _orthogonal_points(point, anchor),
        path_context,
        net_name=net_name,
        start_terminal=terminal,
        end_terminal=None,
    )


def _default_label_sides(side: PortSide, *, axis_locked: bool) -> tuple[PortSide, ...]:
    if axis_locked and side in {"NORTH", "SOUTH"}:
        return ("EAST", "WEST")
    return (side,)


def _label_candidate_sides(side: PortSide, *, axis_locked: bool) -> tuple[PortSide, ...]:
    if axis_locked and side in {"NORTH", "SOUTH"}:
        return ("EAST", "WEST")
    if axis_locked:
        return (side,)
    return (side, _opposite_side(side), *_perpendicular_sides(side))


def _perpendicular_sides(side: PortSide) -> tuple[PortSide, PortSide]:
    if side in {"WEST", "EAST"}:
        return ("NORTH", "SOUTH")
    return ("WEST", "EAST")


def _label_side_penalty(preferred: PortSide, candidate: PortSide) -> float:
    if candidate == preferred:
        return 0.0
    if candidate == _opposite_side(preferred):
        return 50_000.0
    return 25_000.0


def _axis_label_anchor(
    net_name: str,
    point: tuple[float, float],
    side: PortSide,
    offset: float,
) -> tuple[tuple[float, float], Literal["left", "right"]]:
    del net_name
    if side == "WEST":
        return (_snap(point[0] - offset), point[1]), "right"
    if side == "EAST":
        return (_snap(point[0] + offset), point[1]), "left"
    if side == "NORTH":
        return (point[0], _snap(point[1] - offset)), "left"
    return (point[0], _snap(point[1] + offset)), "left"


def _segment_rect(start: tuple[float, float], end: tuple[float, float]) -> Rect:
    return Rect(min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1]))


def _orthogonal_points(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
    if abs(start[0] - end[0]) < 0.001 or abs(start[1] - end[1]) < 0.001:
        return [start, end]
    mid_x = _snap((start[0] + end[0]) / 2)
    return [start, (mid_x, start[1]), (mid_x, end[1]), end]


def _ports_for(placed: dict[str, PlacedComponent]) -> dict[str, tuple[float, float]]:
    ports: dict[str, tuple[float, float]] = {}
    for component in placed.values():
        ports.update(component.ports)
    return ports


def _port_sides_for(placed: dict[str, PlacedComponent]) -> dict[str, PortSide]:
    port_sides: dict[str, PortSide] = {}
    for component in placed.values():
        port_sides.update(component.port_sides)
    return port_sides


def _normalize_assembly(assembly: Assembly) -> Assembly:
    dx = _snap(-assembly.rect.left)
    dy = _snap(-assembly.rect.top)
    items = tuple(_translate_item(item, dx, dy) for item in assembly.items)
    ports = {key: _translate_point(point, dx, dy) for key, point in assembly.ports.items()}
    rect = Rect(0.0, 0.0, _snap(assembly.rect.width), _snap(assembly.rect.height))
    return replace(assembly, items=items, ports=ports, rect=rect)


def _assembly_fits_content(assembly: Assembly) -> bool:
    content = usable_page_rect_for_paper(PAPER)
    if content is None:
        return True
    return assembly.rect.width <= content.width and assembly.rect.height <= content.height


def _best_pack_placements(
    assemblies: list[Assembly],
    contents: tuple[Rect, ...],
    symbol_library: dict[str, SymbolInfo],
    initial_blockers: tuple[Rect, ...],
) -> tuple[list[Assembly], dict[str, tuple[float, float]]]:
    ordered = sorted(assemblies, key=_assembly_area_order_key)
    best: tuple[tuple[int, float, float, float], list[Assembly], dict[str, tuple[float, float]]] | None = None
    for content in contents:
        placements = _pack_ordered_assemblies_geometry(
            ordered,
            content,
            symbol_library,
            initial_blockers,
        )
        conflicts, overlap, area = _pack_placement_quality(
            ordered,
            placements,
            symbol_library,
            initial_blockers,
        )
        key = (-len(placements), conflicts, overlap, area)
        if best is None or key < best[0]:
            best = (key, ordered, placements)
        if len(placements) == len(ordered) and conflicts == 0:
            return ordered, placements
    assert best is not None
    return best[1], best[2]


def _pack_placement_quality(
    ordered: list[Assembly],
    placements: dict[str, tuple[float, float]],
    symbol_library: dict[str, SymbolInfo],
    initial_blockers: tuple[Rect, ...],
) -> tuple[int, float, float]:
    visible: list[Rect] = list(initial_blockers)
    conflicts = 0
    overlap = 0.0
    bounds: Rect | None = None
    for assembly in ordered:
        placement = placements.get(assembly.id)
        if placement is None:
            continue
        visible_boxes, route_boxes = _assembly_pack_boxes(assembly, symbol_library)
        translated_visible = [_translate_rect(box, placement[0], placement[1]) for box in visible_boxes]
        index = _RectIndex(visible)
        for box in translated_visible:
            for placed in index.query(box):
                area = _overlap_area(box, placed)
                if area <= 0.0:
                    continue
                conflicts += 1
                overlap += area
        visible.extend(translated_visible)
        assembly_bounds = _translated_bounds([*visible_boxes, *route_boxes], placement)
        if assembly_bounds is None:
            continue
        bounds = assembly_bounds if bounds is None else Rect(
            min(bounds.left, assembly_bounds.left),
            min(bounds.top, assembly_bounds.top),
            max(bounds.right, assembly_bounds.right),
            max(bounds.bottom, assembly_bounds.bottom),
        )
    packed_area = 0.0 if bounds is None else bounds.width * bounds.height
    return conflicts, overlap, packed_area


def _assembly_area_order_key(assembly: Assembly) -> tuple[float, float, float, str]:
    area = assembly.rect.width * assembly.rect.height
    return (-assembly.rect.height, -assembly.rect.width, -area, assembly.id)


def _pack_ordered_assemblies_geometry(
    ordered: list[Assembly],
    content: Rect,
    symbol_library: dict[str, SymbolInfo],
    initial_blockers: tuple[Rect, ...] = (),
) -> dict[str, tuple[float, float]]:
    placements: dict[str, tuple[float, float]] = {}
    placed_visible_boxes: list[Rect] = list(initial_blockers)
    placed_route_boxes: list[Rect] = []
    placed_rects: list[Rect] = list(initial_blockers)
    for assembly in ordered:
        max_x = content.right - assembly.rect.width
        max_y = content.bottom - assembly.rect.height
        if max_x < content.left - 0.001 or max_y < content.top - 0.001:
            continue
        visible_boxes, route_boxes = _assembly_pack_boxes(assembly, symbol_library)
        score_context = _pack_score_context(placed_visible_boxes, placed_route_boxes, placed_rects)
        best: tuple[float, float, tuple[float, float], list[Rect]] | None = None
        options = _geometry_placement_options(assembly.rect, placed_rects, content)
        for placement in tuple(dict.fromkeys(options)):
            score, visible_overlap, translated = _score_assembly_pack_placement(
                assembly.rect,
                visible_boxes,
                route_boxes,
                placement,
                score_context,
            )
            if best is None or score < best[0]:
                best = (score, visible_overlap, placement, translated)
        if best is None:
            continue
        if best[1] > 0.001:
            for placement in _local_geometry_placement_options(best[2], assembly.rect, content):
                score, visible_overlap, translated = _score_assembly_pack_placement(
                    assembly.rect,
                    visible_boxes,
                    route_boxes,
                    placement,
                    score_context,
                )
                if score < best[0]:
                    best = (score, visible_overlap, placement, translated)
        if best[1] > 0.001:
            for placement in _visible_conflict_escape_options(visible_boxes, best[2], placed_visible_boxes, assembly.rect, content):
                score, visible_overlap, translated = _score_assembly_pack_placement(
                    assembly.rect,
                    visible_boxes,
                    route_boxes,
                    placement,
                    score_context,
                )
                if score < best[0]:
                    best = (score, visible_overlap, placement, translated)
        if best[1] > 0.001:
            for placement in _local_geometry_placement_options(best[2], assembly.rect, content, radius=8):
                score, visible_overlap, translated = _score_assembly_pack_placement(
                    assembly.rect,
                    visible_boxes,
                    route_boxes,
                    placement,
                    score_context,
                )
                if score < best[0]:
                    best = (score, visible_overlap, placement, translated)
        if best[1] > 0.001:
            for placement in _coarse_geometry_placement_options(assembly.rect, content):
                score, visible_overlap, translated = _score_assembly_pack_placement(
                    assembly.rect,
                    visible_boxes,
                    route_boxes,
                    placement,
                    score_context,
                )
                if score < best[0]:
                    best = (score, visible_overlap, placement, translated)
        _score, _visible_overlap, placement, translated = best
        placements[assembly.id] = placement
        placed_visible_boxes.extend(_inflate(box, PACK_GAP) for box in translated)
        placed_route_boxes.extend(_inflate(_translate_rect(box, placement[0], placement[1]), PACK_GAP) for box in route_boxes)
        placed_rect = Rect(
            placement[0] - PACK_GAP,
            placement[1] - PACK_GAP,
            placement[0] + assembly.rect.width + PACK_GAP,
            placement[1] + assembly.rect.height + PACK_GAP,
        )
        placed_rects.append(placed_rect)
    return placements


def _geometry_placement_options(
    rect: Rect,
    placed_rects: list[Rect],
    content: Rect,
) -> tuple[tuple[float, float], ...]:
    max_x = content.right - rect.width
    max_y = content.bottom - rect.height
    if max_x < content.left - 0.001 or max_y < content.top - 0.001:
        return ()
    xs = {content.left, max_x}
    ys = {content.top, max_y}
    for placed in placed_rects:
        xs.update(
            {
                placed.left,
                placed.right + PACK_GAP,
                placed.left - rect.width - PACK_GAP,
            }
        )
        ys.update(
            {
                placed.top,
                placed.bottom + PACK_GAP,
                placed.top - rect.height - PACK_GAP,
            }
        )
    options: list[tuple[float, float]] = []
    for y in sorted(_snap_within(value, content.top, max_y) for value in ys):
        if y < content.top - 0.001 or y > max_y + 0.001:
            continue
        for x in sorted(_snap_within(value, content.left, max_x) for value in xs):
            if x < content.left - 0.001 or x > max_x + 0.001:
                continue
            options.append((_snap(x), _snap(y)))
    return tuple(dict.fromkeys(options))


def _local_geometry_placement_options(
    center: tuple[float, float],
    rect: Rect,
    content: Rect,
    *,
    radius: int = 4,
) -> tuple[tuple[float, float], ...]:
    max_x = content.right - rect.width
    max_y = content.bottom - rect.height
    options: list[tuple[float, float]] = []
    for y_index in range(-radius, radius + 1):
        y = _snap_within(center[1] + y_index * GRID, content.top, max_y)
        if y < content.top - 0.001 or y > max_y + 0.001:
            continue
        for x_index in range(-radius, radius + 1):
            x = _snap_within(center[0] + x_index * GRID, content.left, max_x)
            if x < content.left - 0.001 or x > max_x + 0.001:
                continue
            options.append((_snap(x), _snap(y)))
    return tuple(dict.fromkeys(options))


def _coarse_geometry_placement_options(
    rect: Rect,
    content: Rect,
) -> tuple[tuple[float, float], ...]:
    max_x = content.right - rect.width
    max_y = content.bottom - rect.height
    if max_x < content.left - 0.001 or max_y < content.top - 0.001:
        return ()
    step = SUPPORT_STEP
    options: list[tuple[float, float]] = []
    y = content.top
    while y <= max_y + 0.001:
        x = content.left
        while x <= max_x + 0.001:
            options.append((_snap(x), _snap(y)))
            x += step
        y += step
    options.append((_snap(max_x), _snap(max_y)))
    options.append((_snap(content.left), _snap(max_y)))
    options.append((_snap(max_x), _snap(content.top)))
    return tuple(dict.fromkeys(options))


def _visible_conflict_escape_options(
    boxes: list[Rect],
    placement: tuple[float, float],
    placed_boxes: list[Rect],
    rect: Rect,
    content: Rect,
) -> tuple[tuple[float, float], ...]:
    max_x = content.right - rect.width
    max_y = content.bottom - rect.height
    if max_x < content.left - 0.001 or max_y < content.top - 0.001:
        return ()
    x0, y0 = placement
    xs: set[float] = {x0}
    ys: set[float] = {y0}
    for box in boxes:
        translated = box.translated(x0, y0)
        for placed in placed_boxes:
            if not translated.overlaps(placed):
                continue
            xs.add(placed.left - box.right)
            xs.add(placed.right - box.left)
            ys.add(placed.top - box.bottom)
            ys.add(placed.bottom - box.top)
    options: list[tuple[float, float]] = []
    for y in sorted(_snap_within(value, content.top, max_y) for value in ys):
        for x in sorted(_snap_within(value, content.left, max_x) for value in xs):
            options.append((_snap(x), _snap(y)))
    return tuple(dict.fromkeys(options))


def _score_assembly_pack_placement(
    rect: Rect,
    visible_boxes: list[Rect],
    route_boxes: list[Rect],
    placement: tuple[float, float],
    context: _PackScoreContext,
) -> tuple[float, float, list[Rect]]:
    translated = [_translate_rect(box, placement[0], placement[1]) for box in visible_boxes]
    visible_overlap = 0.0
    visible_conflicts = 0
    for box in translated:
        for placed in context.visible_index.query(box):
            area = _overlap_area(box, placed)
            if area <= 0.0:
                continue
            visible_overlap += area
            visible_conflicts += 1
    route_overlap = 0.0
    route_conflicts = 0
    for route_box in route_boxes:
        box = _translate_rect(route_box, placement[0], placement[1])
        for placed in context.visible_index.query(box):
            area = _overlap_area(box, placed)
            if area <= 0.0:
                continue
            route_overlap += area
            route_conflicts += 1
        for placed in context.route_index.query(box):
            area = _overlap_area(box, placed)
            if area <= 0.0:
                continue
            route_overlap += area * 0.25
            route_conflicts += 1
    for box in translated:
        for placed in context.route_index.query(box):
            area = _overlap_area(box, placed)
            if area <= 0.0:
                continue
            route_overlap += area
            route_conflicts += 1
    assembly_rect = Rect(
        placement[0],
        placement[1],
        placement[0] + rect.width,
        placement[1] + rect.height,
    )
    envelope_overlap = 0.0
    for placed in context.envelope_index.query(assembly_rect):
        envelope_overlap += _overlap_area(assembly_rect, placed)
    packed_bounds = _translated_bounds([*visible_boxes, *route_boxes], placement) or assembly_rect
    if context.placed_bounds is None:
        left = min(assembly_rect.left, packed_bounds.left)
        top = min(assembly_rect.top, packed_bounds.top)
        right = max(assembly_rect.right, packed_bounds.right)
        bottom = max(assembly_rect.bottom, packed_bounds.bottom)
    else:
        left = min(assembly_rect.left, context.placed_bounds.left, packed_bounds.left)
        top = min(assembly_rect.top, context.placed_bounds.top, packed_bounds.top)
        right = max(assembly_rect.right, context.placed_bounds.right, packed_bounds.right)
        bottom = max(assembly_rect.bottom, context.placed_bounds.bottom, packed_bounds.bottom)
    area = (right - left) * (bottom - top)
    score = (
        visible_conflicts * 1_000_000_000.0
        + visible_overlap * 1_000_000.0
        + route_conflicts * 100_000_000.0
        + route_overlap * 1_000_000.0
        + envelope_overlap * 10_000.0
        + area
        + _center_preference(assembly_rect, context.placed_bounds, packed_bounds) * 25.0
        + placement[1] * 0.01
        + placement[0] * 0.001
    )
    return score, float(visible_conflicts), translated


def _center_preference(
    assembly_rect: Rect,
    placed_bounds: Rect | None,
    packed_bounds: Rect,
) -> float:
    if placed_bounds is None:
        return 0.0
    cluster_center_x = (placed_bounds.left + placed_bounds.right) / 2
    cluster_center_y = (placed_bounds.top + placed_bounds.bottom) / 2
    assembly_center_x = (assembly_rect.left + assembly_rect.right) / 2
    assembly_center_y = (assembly_rect.top + assembly_rect.bottom) / 2
    packed_span = max(1.0, packed_bounds.width, packed_bounds.height)
    return (abs(assembly_center_x - cluster_center_x) + abs(assembly_center_y - cluster_center_y)) / packed_span


def _pack_score_context(
    placed_visible_boxes: list[Rect],
    placed_route_boxes: list[Rect],
    placed_rects: list[Rect],
) -> _PackScoreContext:
    placed_bounds = _rects_bounds([*placed_visible_boxes, *placed_route_boxes])
    return _PackScoreContext(
        visible_index=_RectIndex(placed_visible_boxes),
        route_index=_RectIndex(placed_route_boxes),
        envelope_index=_RectIndex(placed_rects),
        placed_bounds=placed_bounds,
    )


def _path_context(
    avoid_elements: list[LayoutElement],
    existing_segments: list[tuple[frozenset[str], tuple[float, float, float, float]]],
) -> _PathContext:
    return _PathContext(
        avoid_index=_LayoutElementIndex(avoid_elements),
        existing_index=_SegmentIndex(existing_segments),
    )


def _rects_bounds(rects: list[Rect]) -> Rect | None:
    if not rects:
        return None
    return Rect(
        min(rect.left for rect in rects),
        min(rect.top for rect in rects),
        max(rect.right for rect in rects),
        max(rect.bottom for rect in rects),
    )


def _translated_bounds(rects: list[Rect], offset: tuple[float, float]) -> Rect | None:
    if not rects:
        return None
    dx, dy = offset
    return Rect(
        min(rect.left for rect in rects) + dx,
        min(rect.top for rect in rects) + dy,
        max(rect.right for rect in rects) + dx,
        max(rect.bottom for rect in rects) + dy,
    )


class _RectIndex:
    def __init__(self, rects: list[Rect], *, cell_size: float = 25.4) -> None:
        self._rects = tuple(rects)
        self._cell_size = cell_size
        self._cells: dict[tuple[int, int], list[int]] = {}
        for index, rect in enumerate(self._rects):
            for key in _grid_cell_keys(rect, self._cell_size):
                self._cells.setdefault(key, []).append(index)

    def query(self, rect: Rect) -> tuple[Rect, ...]:
        if not self._rects:
            return ()
        indexes: set[int] = set()
        for key in _grid_cell_keys(rect, self._cell_size):
            indexes.update(self._cells.get(key, ()))
        return tuple(self._rects[index] for index in indexes)


class _LayoutElementIndex:
    def __init__(self, elements: list[LayoutElement], *, cell_size: float = 25.4) -> None:
        self._elements = tuple(elements)
        self._cell_size = cell_size
        self._cells: dict[tuple[int, int], list[int]] = {}
        for index, element in enumerate(self._elements):
            for key in _grid_cell_keys(element.rect, self._cell_size):
                self._cells.setdefault(key, []).append(index)

    def query_segment(self, segment: tuple[float, float, float, float]) -> tuple[LayoutElement, ...]:
        if not self._elements:
            return ()
        rect = _segment_tuple_rect(segment)
        indexes: set[int] = set()
        for key in _grid_cell_keys(rect, self._cell_size):
            indexes.update(self._cells.get(key, ()))
        return tuple(self._elements[index] for index in indexes)


class _SegmentIndex:
    def __init__(
        self,
        segments: list[tuple[frozenset[str], tuple[float, float, float, float]]],
        *,
        cell_size: float = 25.4,
    ) -> None:
        self._segments = tuple(segments)
        self._cell_size = cell_size
        self._cells: dict[tuple[int, int], list[int]] = {}
        for index, (_nets, segment) in enumerate(self._segments):
            for key in _grid_cell_keys(_segment_tuple_rect(segment), self._cell_size):
                self._cells.setdefault(key, []).append(index)

    def query_segment(
        self,
        segment: tuple[float, float, float, float],
    ) -> tuple[tuple[frozenset[str], tuple[float, float, float, float]], ...]:
        if not self._segments:
            return ()
        indexes: set[int] = set()
        for key in _grid_cell_keys(_segment_tuple_rect(segment), self._cell_size):
            indexes.update(self._cells.get(key, ()))
        return tuple(self._segments[index] for index in indexes)


def _grid_cell_keys(rect: Rect, cell_size: float) -> tuple[tuple[int, int], ...]:
    left = int(floor(rect.left / cell_size))
    right = int(floor(rect.right / cell_size))
    top = int(floor(rect.top / cell_size))
    bottom = int(floor(rect.bottom / cell_size))
    return tuple((x, y) for x in range(left, right + 1) for y in range(top, bottom + 1))


def _segment_tuple_rect(segment: tuple[float, float, float, float]) -> Rect:
    x1, y1, x2, y2 = segment
    return Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _assembly_pack_boxes(assembly: Assembly, symbol_library: dict[str, SymbolInfo]) -> tuple[list[Rect], list[Rect]]:
    geometry = placed_items_geometry(assembly.items, symbol_library=symbol_library)
    visible_boxes = [box.rect for box in geometry.boxes if box.kind != "no_connect"]
    route_boxes: list[Rect] = []
    for segment in geometry.segments:
        if segment.kind != "wire":
            continue
        x1, y1, x2, y2 = segment.wire_segment()
        route_boxes.append(_inflate(Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), GRID / 2))
    return visible_boxes, route_boxes


def _occupied_rects(
    items: tuple[PlacedItem, ...],
    symbol_library: dict[str, SymbolInfo],
    *,
    margin: float,
) -> list[Rect]:
    geometry = placed_items_geometry(items, symbol_library=symbol_library)
    return [_inflate(box.rect, margin) for box in geometry.boxes]


def _port_body_edge(
    placed: PlacedComponent,
    endpoint_key: str,
    side: PortSide,
    symbol_library: dict[str, SymbolInfo],
) -> float:
    point = placed.ports[endpoint_key]
    body_boxes = [
        box.rect
        for box in placed_items_geometry(placed.items, symbol_library=symbol_library).boxes
        if box.kind == "symbol_body"
    ]
    if len(body_boxes) <= 1:
        if side == "WEST":
            return placed.rect.left
        if side == "EAST":
            return placed.rect.right
        if side == "NORTH":
            return placed.rect.top
        return placed.rect.bottom

    def distance(rect: Rect) -> float:
        if side in {"WEST", "EAST"}:
            in_band = rect.top - GRID <= point[1] <= rect.bottom + GRID
            edge = rect.left if side == "WEST" else rect.right
            return (0.0 if in_band else min(abs(point[1] - rect.top), abs(point[1] - rect.bottom)) * 100.0) + abs(point[0] - edge)
        in_band = rect.left - GRID <= point[0] <= rect.right + GRID
        edge = rect.top if side == "NORTH" else rect.bottom
        return (0.0 if in_band else min(abs(point[0] - rect.left), abs(point[0] - rect.right)) * 100.0) + abs(point[1] - edge)

    rect = min(body_boxes, key=distance)
    if side == "WEST":
        return rect.left
    if side == "EAST":
        return rect.right
    if side == "NORTH":
        return rect.top
    return rect.bottom


def _direct_support_wire_allowed(
    root: PlacedComponent,
    support: PlacedComponent,
    net_name: str,
    root_point: tuple[float, float],
    support_point: tuple[float, float],
) -> bool:
    del support
    distance = _manhattan(root_point, support_point)
    if not _is_power_net(net_name):
        return distance <= DIRECT_LOCAL_WIRE_LIMIT
    return distance <= DIRECT_LOCAL_WIRE_LIMIT


def _rail_profile_width(spans: list[Rect]) -> float:
    if not spans:
        return 0.0
    return sum(span.width for span in spans) + GRID * 2 * (len(spans) - 1)


def _translate_rect(rect: Rect, dx: float, dy: float) -> Rect:
    return Rect(rect.left + dx, rect.top + dy, rect.right + dx, rect.bottom + dy)


def _translate_item(item: PlacedItem, dx: float, dy: float) -> PlacedItem:
    if isinstance(item, PlacedSymbol):
        return replace(item, at=_translate_point(item.at, dx, dy), properties=tuple(_translate_property(prop, dx, dy) for prop in item.properties))
    if isinstance(item, PlacedSheetBlock):
        return replace(
            item,
            at=_translate_point(item.at, dx, dy),
            sheet_name_at=_translate_point(item.sheet_name_at, dx, dy),
            sheet_file_at=_translate_point(item.sheet_file_at, dx, dy),
            pins=tuple(replace(pin, at=_translate_point(pin.at, dx, dy)) for pin in item.pins),
        )
    if isinstance(item, PlacedWire):
        return replace(item, start=_translate_point(item.start, dx, dy), end=_translate_point(item.end, dx, dy))
    if isinstance(item, PlacedJunction):
        return replace(item, at=_translate_point(item.at, dx, dy))
    if isinstance(item, PlacedLabel):
        return replace(item, at=_translate_point(item.at, dx, dy))
    if isinstance(item, PlacedHierarchicalLabel):
        return replace(item, at=_translate_point(item.at, dx, dy))
    if isinstance(item, PlacedText):
        return replace(item, at=_translate_point(item.at, dx, dy))
    if isinstance(item, PlacedNoConnect):
        return replace(item, at=_translate_point(item.at, dx, dy))
    return item


def _translate_property(prop: PlacedProperty, dx: float, dy: float) -> PlacedProperty:
    return replace(prop, at=_translate_point(prop.at, dx, dy))


def _symbol_property_value(symbol: PlacedSymbol, name: str) -> str | None:
    for property_ in symbol.properties:
        if property_.name == name:
            return property_.value
    return None


def _translate_point(point: tuple[float, float], dx: float, dy: float) -> tuple[float, float]:
    return (_snap(point[0] + dx), _snap(point[1] + dy))


def _manhattan(first: tuple[float, float], second: tuple[float, float]) -> float:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _items_rect(items: tuple[PlacedItem, ...], symbol_library: dict[str, SymbolInfo]) -> Rect | None:
    geometry = placed_items_geometry(items, symbol_library=symbol_library)
    rects = [element.rect for element in geometry.boxes]
    for segment in geometry.segments:
        x1, y1, x2, y2 = segment.wire_segment()
        rects.append(Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    if not rects:
        return None
    return Rect(min(rect.left for rect in rects), min(rect.top for rect in rects), max(rect.right for rect in rects), max(rect.bottom for rect in rects))


def _local_net_root(records: list[NetEndpoint], components: dict[str, Component]) -> NetEndpoint:
    return max(records, key=lambda record: (not components[record.component_id].passive, len(components[record.component_id].ports), record.component_id))


def _symbol_units(units: list[int] | None, symbol_info: SymbolInfo | None) -> tuple[int, ...]:
    if units:
        return tuple(sorted(set(units)))
    if symbol_info is None:
        return (1,)
    symbol_units = sorted({pin.unit for pin in symbol_info.pins if pin.unit != 0})
    return tuple(symbol_units or [1])


def _unit_symbol_info(symbol_info: SymbolInfo | None, unit: int) -> SymbolInfo | None:
    if symbol_info is None:
        return None
    return replace(symbol_info, pins=[replace(pin, unit=1) for pin in symbol_info.pins if pin.unit in {0, unit}])


def _resolved_endpoint_pin(symbol_info: SymbolInfo | None, endpoint: ResolvedEndpoint) -> SymbolPin | None:
    if symbol_info is None:
        return None
    for pin in symbol_info.pins:
        if endpoint.pin_number is not None and pin.number != endpoint.pin_number:
            continue
        if endpoint.pin_name is not None and pin.name != endpoint.pin_name and pin.number != endpoint.pin_name:
            continue
        return pin
    return None


def _source_endpoint_pin(symbol_info: SymbolInfo | None, endpoint: Any) -> SymbolPin | None:
    pins = _source_endpoint_pins(symbol_info, endpoint)
    return pins[0] if pins else None


def _source_endpoint_pins(symbol_info: SymbolInfo | None, endpoint: Any) -> list[SymbolPin]:
    if symbol_info is None:
        return []
    matches: list[SymbolPin] = []
    pin_number = getattr(endpoint, "pin_number", None)
    pin_name = getattr(endpoint, "pin_name", None)
    for pin in symbol_info.pins:
        if pin_number is not None and pin.number != pin_number:
            continue
        if pin_name is not None and pin.name != pin_name and pin.number != pin_name:
            continue
        matches.append(pin)
    return matches if getattr(endpoint, "all_matching", False) else matches[:1]


def _pin_side(symbol_info: SymbolInfo | None, pin: SymbolPin) -> PortSide:
    return {
        "left": "WEST",
        "right": "EAST",
        "top": "NORTH",
        "bottom": "SOUTH",
    }[symbol_pin_side(symbol_info, pin)]


def _is_passive(ref: str, symbol_info: SymbolInfo | None) -> bool:
    if ref.startswith(("R", "C", "L", "D", "F", "TP", "Y")):
        return True
    if symbol_info is None:
        return False
    return len(symbol_info.pins) <= 3 and {pin.electrical_type for pin in symbol_info.pins} <= {"passive", "power_in", "power_out", "input", "output"}


def _is_loose_marker_component(component: Component) -> bool:
    if component.kind != "symbol" or component.ref is None or component.symbol_decl is None:
        return False
    if len(component.ports) != 1:
        return False
    return component.ref.startswith("TP") or "TestPoint" in component.symbol_decl.lib


def _is_local_support_component(component: Component) -> bool:
    if not component.passive or component.kind != "symbol" or component.ref is None:
        return False
    if _is_loose_marker_component(component):
        return False
    if len(component.ports) > 4:
        return False
    return component.ref.startswith(("R", "C", "L", "D", "F", "Y", "SW"))


def _is_core_component(component: Component) -> bool:
    if component.passive or component.kind != "symbol" or component.ref is None:
        return False
    return component.ref.startswith("U")


def _is_decoupling_cap(component: Component) -> bool:
    return component.passive and component.ref is not None and component.ref.startswith("C") and len(component.ports) == 2


def _is_capacitor_component(component: Component) -> bool:
    return component.passive and component.kind == "symbol" and component.ref is not None and component.ref.startswith("C")


def _is_oscillator_bridge_component(component: Component) -> bool:
    return (
        component.passive
        and component.kind == "symbol"
        and component.ref is not None
        and component.ref.startswith("Y")
        and len(component.ports) >= 2
    )


def _is_power_net(net_name: str) -> bool:
    upper = net_name.upper()
    if _is_ground_net(net_name):
        return True
    if re.search(r"_(SCL|SDA|RXD?|TXD?|INT|RST|RESET|EN|CS|MISO|MOSI|SCLK|CLK|DP|DN|D[0-9]+[NP]?)$", upper):
        return False
    if re.search(r"(?:^|[_+-])\d+(?:V\d*|\.\d+V)(?:[A-Z0-9_]*)?$", upper):
        return True
    return any(token in upper for token in ("VCC", "VDD", "AVDD", "VREF", "VBAT", "VBUS", "1V", "3V3", "5V", "12V"))


def _qualified_prefix_from_names(net_names: object) -> str | None:
    counts: dict[str, int] = {}
    for net_name in net_names:
        if not isinstance(net_name, str):
            continue
        separator = net_name.find("_")
        if separator <= 0:
            continue
        prefix = net_name[: separator + 1]
        if not any(character.isspace() for character in prefix) and "+" not in prefix:
            continue
        counts[prefix] = counts.get(prefix, 0) + 1
    if not counts:
        return None
    prefix, count = max(counts.items(), key=lambda item: (item[1], len(item[0])))
    return prefix if count >= 2 else None


def _sheet_net_prefix(sheet_path: str, net_names: object) -> str | None:
    local_name = sheet_path.strip("/").split("/")[-1]
    if local_name:
        candidate = f"{local_name}_"
        count = sum(
            1
            for net_name in net_names
            if isinstance(net_name, str)
            and net_name.startswith(candidate)
            and len(net_name) > len(candidate)
        )
        if count >= 2:
            return candidate
    return _qualified_prefix_from_names(net_names)


def _is_ground_net(net_name: str) -> bool:
    upper = net_name.upper()
    return upper in {"GND", "DGND", "AGND", "PGND"} or upper.endswith("_GND")


def _split_sheet_ports(ports: list[tuple[str, PinDirection]]) -> tuple[list[tuple[str, PinDirection]], list[tuple[str, PinDirection]]]:
    left: list[tuple[str, PinDirection]] = []
    right: list[tuple[str, PinDirection]] = []
    for name, direction in ports:
        if direction in {"input", "power_in"}:
            left.append((name, direction))
        else:
            right.append((name, direction))
    return left, right


def _sheet_pin_shape(direction: PinDirection) -> str:
    if direction in {"power_in", "power_out", "passive"}:
        return "passive"
    return "input" if direction == "input" else "output" if direction == "output" else "bidirectional"


def _sheet_pin_step(pin_count: int) -> float:
    if pin_count <= 12:
        return SHEET_PIN_STEP
    if pin_count <= 28:
        return 3.81
    return 2.54


def _side_vector(side: PortSide) -> tuple[float, float]:
    if side == "WEST":
        return (-1.0, 0.0)
    if side == "EAST":
        return (1.0, 0.0)
    if side == "NORTH":
        return (0.0, -1.0)
    return (0.0, 1.0)


def _perp_vector(side: PortSide) -> tuple[float, float]:
    return (0.0, 1.0) if side in {"WEST", "EAST"} else (1.0, 0.0)


def _lane_offsets(*, limit: int = 10) -> tuple[float, ...]:
    offsets = [0.0]
    for index in range(1, limit + 1):
        offsets.extend((index * SUPPORT_STEP, -index * SUPPORT_STEP))
    return tuple(offsets)


def _port_axis_offsets(*, limit: int = 10) -> tuple[float, ...]:
    offsets = [0.0]
    for index in range(1, limit + 1):
        distance = index * SUPPORT_STEP
        offsets.extend((distance, -distance))
    return tuple(offsets)


def _inflate(rect: Rect, amount: float) -> Rect:
    return Rect(rect.left - amount, rect.top - amount, rect.right + amount, rect.bottom + amount)


def _overlap_area(first: Rect, second: Rect) -> float:
    if not first.overlaps(second):
        return 0.0
    return max(0.0, min(first.right, second.right) - max(first.left, second.left)) * max(0.0, min(first.bottom, second.bottom) - max(first.top, second.top))


def _indexed_overlap_area(rect: Rect, index: _RectIndex) -> float:
    return sum(_overlap_area(rect, item) for item in index.query(rect))


def _path_rect_overlap_area(
    start: tuple[float, float],
    end: tuple[float, float],
    rects: tuple[Rect, ...],
) -> float:
    points = _orthogonal_points(start, end)
    overlap = 0.0
    for first, second in zip(points, points[1:]):
        stub_rect = _inflate(_segment_rect(first, second), GRID / 4)
        overlap += sum(_overlap_area(stub_rect, item) for item in rects)
    return overlap


def _route_avoid_elements(
    items: list[PlacedItem],
    symbol_library: dict[str, SymbolInfo],
) -> list[LayoutElement]:
    return list(placed_items_geometry(tuple(items), symbol_library=symbol_library).boxes)


def _wire_avoid_rects(items: list[PlacedItem]) -> list[Rect]:
    rects: list[Rect] = []
    for item in items:
        if not isinstance(item, PlacedWire):
            continue
        rects.append(_inflate(_segment_rect(item.start, item.end), GRID / 4))
    return rects


def _symbol_component_id(ref: str, unit: int) -> str:
    return f"symbol:{ref}:{unit}"


def _sheet_component_id(child_name: str) -> str:
    return f"sheet:{_safe_id(child_name)}"


def _component_ref_sort_key(component_id: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"symbol:([A-Za-z#]+)(\d+):\d+", component_id)
    if match:
        return match.group(1), int(match.group(2)), component_id
    return component_id, 0, component_id


def _symbol_endpoint_key(ref: str, unit: int, pin_number: str) -> str:
    return f"symbol:{ref}:{unit}:{pin_number}"


def _sheet_endpoint_key(child_name: str, port_name: str) -> str:
    return f"sheet:{child_name}:{port_name}"


def _power_endpoint_key(index: int, net_name: str) -> str:
    return f"power:{index}:{net_name}"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _snap(value: float) -> float:
    return snap_grid(value, PIN_GRID)


def _snap_down(value: float) -> float:
    return round(floor(value / PIN_GRID + 1e-9) * PIN_GRID, 2)


def _snap_within(value: float, low: float, high: float) -> float:
    snapped = _snap(value)
    if snapped > high + 0.001:
        snapped = _snap_down(high)
    if snapped < low - 0.001:
        snapped = _snap(low)
    return snapped


def _same_point(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return abs(first[0] - second[0]) < 0.001 and abs(first[1] - second[1]) < 0.001

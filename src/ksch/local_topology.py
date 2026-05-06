from collections.abc import Iterable
from dataclasses import dataclass

from ksch.geometry import PinPoint, is_vertical_two_pin_symbol
from ksch.model.endpoint import EndpointKind
from ksch.placement import _is_anchor_ref, _is_groundish_net, _is_powerish_net
from ksch.resolver import ResolvedEndpoint, ResolvedProject


@dataclass(frozen=True)
class LocalTopologyEndpoint:
    net_name: str
    endpoint_text: str
    point: PinPoint
    ref: str
    pin_number: str
    is_anchor: bool
    is_two_pin_passive: bool


@dataclass(frozen=True)
class LocalTopologyNet:
    name: str
    endpoints: tuple[LocalTopologyEndpoint, ...]


@dataclass(frozen=True)
class AnchorPassiveNet:
    net_name: str
    anchor: LocalTopologyEndpoint
    passive: LocalTopologyEndpoint

    @property
    def endpoints(self) -> tuple[tuple[str, PinPoint], ...]:
        return (
            (self.anchor.endpoint_text, self.anchor.point),
            (self.passive.endpoint_text, self.passive.point),
        )


@dataclass(frozen=True)
class PassiveContinuationNet:
    net_name: str
    anchor: LocalTopologyEndpoint
    source: LocalTopologyEndpoint
    passive: LocalTopologyEndpoint

    @property
    def endpoints(self) -> tuple[tuple[str, PinPoint], ...]:
        return (
            (self.source.endpoint_text, self.source.point),
            (self.passive.endpoint_text, self.passive.point),
        )


@dataclass(frozen=True)
class LocalTopology:
    nets: tuple[LocalTopologyNet, ...]
    anchor_passive_nets: tuple[AnchorPassiveNet, ...]
    passive_continuation_nets: tuple[PassiveContinuationNet, ...] = ()


def build_local_topology(
    project: ResolvedProject,
    sheet_path: str,
    net_points_by_name: dict[str, list[tuple[str, PinPoint]]],
) -> LocalTopology:
    """Classify placed sheet endpoints into local circuit relationships.

    This stage is intentionally about electrical/topological relationships, not
    S-expression emission. It lets routing consume facts like "this compact net
    connects an IC pin to one terminal of a two-pin support part" without
    re-inferring that intent from labels inside the generic net fallback.
    """

    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return LocalTopology(nets=(), anchor_passive_nets=())

    point_by_endpoint = {
        endpoint_text: point
        for net_points in net_points_by_name.values()
        for endpoint_text, point in net_points
    }
    topology_nets: list[LocalTopologyNet] = []
    anchor_passive_nets: list[AnchorPassiveNet] = []

    for net_name, resolved_endpoints in sorted(resolved_sheet.nets.items()):
        endpoints: list[LocalTopologyEndpoint] = []
        for endpoint in resolved_endpoints:
            topology_endpoint = _topology_endpoint(
                project,
                sheet_path,
                net_name,
                endpoint,
                point_by_endpoint,
            )
            if topology_endpoint is not None:
                endpoints.append(topology_endpoint)
        topology_net = LocalTopologyNet(name=net_name, endpoints=tuple(endpoints))
        topology_nets.append(topology_net)
        anchor_passive = _anchor_passive_net(topology_net)
        if anchor_passive is not None:
            anchor_passive_nets.append(anchor_passive)

    passive_continuation_nets = _passive_continuation_nets(
        topology_nets,
        anchor_passive_nets,
    )

    return LocalTopology(
        nets=tuple(topology_nets),
        anchor_passive_nets=tuple(anchor_passive_nets),
        passive_continuation_nets=tuple(passive_continuation_nets),
    )


def anchor_passive_endpoint_texts(
    routes: Iterable[AnchorPassiveNet],
) -> set[str]:
    return {
        endpoint.endpoint_text
        for route in routes
        for endpoint in (route.anchor, route.passive)
    }


def _topology_endpoint(
    project: ResolvedProject,
    sheet_path: str,
    net_name: str,
    endpoint: ResolvedEndpoint,
    point_by_endpoint: dict[str, PinPoint],
) -> LocalTopologyEndpoint | None:
    if endpoint.kind is not EndpointKind.SYMBOL_PIN:
        return None
    if endpoint.ref is None or endpoint.pin_number is None:
        return None
    point = point_by_endpoint.get(endpoint.text)
    if point is None:
        return None
    symbol_decl = project.source.sheets[sheet_path].symbols.get(endpoint.ref)
    if symbol_decl is None:
        return None
    symbol_info = project.symbol_library.get(symbol_decl.lib)
    return LocalTopologyEndpoint(
        net_name=net_name,
        endpoint_text=endpoint.text,
        point=point,
        ref=endpoint.ref,
        pin_number=endpoint.pin_number,
        is_anchor=_is_anchor_ref(endpoint.ref),
        is_two_pin_passive=is_vertical_two_pin_symbol(symbol_info),
    )


def _anchor_passive_net(topology_net: LocalTopologyNet) -> AnchorPassiveNet | None:
    if len(topology_net.endpoints) != 2:
        return None
    anchors = tuple(endpoint for endpoint in topology_net.endpoints if endpoint.is_anchor)
    passives = tuple(
        endpoint
        for endpoint in topology_net.endpoints
        if not endpoint.is_anchor and endpoint.is_two_pin_passive
    )
    if len(anchors) != 1 or len(passives) != 1:
        return None
    return AnchorPassiveNet(
        net_name=topology_net.name,
        anchor=anchors[0],
        passive=passives[0],
    )


def _passive_continuation_nets(
    topology_nets: list[LocalTopologyNet],
    anchor_passive_nets: list[AnchorPassiveNet],
) -> list[PassiveContinuationNet]:
    anchored_by_ref = {
        route.passive.ref: route.anchor
        for route in anchor_passive_nets
        if not _is_groundish_net(route.net_name) and not _is_powerish_net(route.net_name)
    }
    continuation_nets: list[PassiveContinuationNet] = []
    for topology_net in topology_nets:
        if _is_groundish_net(topology_net.name) or _is_powerish_net(topology_net.name):
            continue
        if len(topology_net.endpoints) != 2:
            continue
        if not all(
            endpoint.is_two_pin_passive and not endpoint.is_anchor
            for endpoint in topology_net.endpoints
        ):
            continue
        first, second = topology_net.endpoints
        first_anchor = anchored_by_ref.get(first.ref)
        second_anchor = anchored_by_ref.get(second.ref)
        if (first_anchor is None) == (second_anchor is None):
            continue
        if first_anchor is not None:
            source, passive, anchor = first, second, first_anchor
        else:
            if second_anchor is None:
                continue
            source, passive, anchor = second, first, second_anchor
        continuation_nets.append(
            PassiveContinuationNet(
                net_name=topology_net.name,
                anchor=anchor,
                source=source,
                passive=passive,
            )
        )
    return continuation_nets

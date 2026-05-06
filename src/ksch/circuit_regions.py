from dataclasses import dataclass
from typing import Literal

from ksch.circuit_motifs import (
    SheetCircuitMotifs,
    build_sheet_circuit_motifs,
    is_anchor_ref,
    is_groundish_net,
    symbol_prefix,
)
from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedEndpoint, ResolvedProject

CircuitRegionKind = Literal["anchor_support", "rail_bank"]


@dataclass(frozen=True)
class CircuitRegion:
    id: str
    kind: CircuitRegionKind
    refs: tuple[str, ...]
    nets: tuple[str, ...]
    anchor_ref: str | None = None

    def contains_ref(self, ref: str) -> bool:
        return ref == self.anchor_ref or ref in self.refs


@dataclass(frozen=True)
class SheetCircuitRegions:
    regions: tuple[CircuitRegion, ...]

    def region_for_ref(self, ref: str) -> CircuitRegion | None:
        return next((region for region in self.regions if region.contains_ref(ref)), None)

    def same_region(self, first_ref: str, second_ref: str) -> bool:
        first_region = self.region_for_ref(first_ref)
        return first_region is not None and first_region.contains_ref(second_ref)

    def refs_for_anchor(self, anchor_ref: str) -> tuple[str, ...]:
        region = next(
            (
                candidate
                for candidate in self.regions
                if candidate.kind == "anchor_support" and candidate.anchor_ref == anchor_ref
            ),
            None,
        )
        return region.refs if region is not None else ()


def build_sheet_circuit_regions(
    project: ResolvedProject,
    sheet_path: str,
    *,
    motifs: SheetCircuitMotifs | None = None,
) -> SheetCircuitRegions:
    """Group sheet motifs into local circuit regions before placement.

    Motifs describe local shapes such as "this is a shunt" or "these two passives
    form a tap stack." Regions describe ownership: which local shapes should be
    placed together around the same anchor or rail. This is a compiler/layout
    stage, not KiCad emission behavior.
    """

    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return SheetCircuitRegions(regions=())

    sheet = project.source.sheets[sheet_path]
    motifs = motifs or build_sheet_circuit_motifs(project, sheet_path)
    refs_by_net = _refs_by_net(resolved_sheet.nets)
    nets_by_ref = _nets_by_ref(resolved_sheet.nets)
    rail_bank_refs = {ref for motif in motifs.rail_banks for ref in motif.refs}
    two_pin_refs = {
        motif.ref
        for motif in motifs.two_pin_refs
        if motif.ref in sheet.symbols
        and motif.ref not in rail_bank_refs
        and not is_anchor_ref(motif.ref)
    }
    anchor_refs = {ref for ref in sheet.symbols if is_anchor_ref(ref)}

    owner_by_ref: dict[str, str] = {}
    candidate_owners: dict[str, list[tuple[int, str]]] = {}

    def add_candidate(ref: str, anchor_ref: str, priority: int) -> None:
        if ref in two_pin_refs and anchor_ref in anchor_refs:
            candidate_owners.setdefault(ref, []).append((priority, anchor_ref))

    for stack in motifs.tap_stacks:
        add_candidate(stack.top_ref, stack.anchor_ref, -1)
        add_candidate(stack.bottom_ref, stack.anchor_ref, -1)

    for net_name, net_refs in refs_by_net.items():
        if is_groundish_net(net_name):
            continue
        anchors = sorted(net_refs & anchor_refs, key=_anchor_score)
        if not anchors:
            continue
        owner = anchors[0]
        for ref in sorted(net_refs & two_pin_refs):
            add_candidate(ref, owner, 0)

    for ref, candidates in sorted(candidate_owners.items()):
        owner_by_ref[ref] = sorted(
            candidates,
            key=lambda item: (item[0], _anchor_score(item[1]), item[1]),
        )[0][1]

    changed = True
    while changed:
        changed = False
        for ref, owner in sorted(tuple(owner_by_ref.items())):
            for net_name in sorted(nets_by_ref.get(ref, set())):
                if is_groundish_net(net_name):
                    continue
                for neighbor in sorted(refs_by_net.get(net_name, set()) & two_pin_refs):
                    if neighbor in owner_by_ref:
                        continue
                    owner_by_ref[neighbor] = owner
                    changed = True

    anchor_regions = [
        CircuitRegion(
            id=f"anchor:{anchor_ref}",
            kind="anchor_support",
            anchor_ref=anchor_ref,
            refs=tuple(sorted(ref for ref, owner in owner_by_ref.items() if owner == anchor_ref)),
            nets=tuple(
                sorted(
                    {
                        net
                        for ref, owner in owner_by_ref.items()
                        if owner == anchor_ref
                        for net in nets_by_ref.get(ref, set())
                    }
                )
            ),
        )
        for anchor_ref in sorted(set(owner_by_ref.values()), key=_anchor_score)
    ]
    rail_regions = [
        CircuitRegion(
            id=f"rail:{motif.top_net}:{motif.bottom_net}",
            kind="rail_bank",
            refs=motif.refs,
            nets=(motif.top_net, motif.bottom_net),
        )
        for motif in motifs.rail_banks
    ]
    regions = tuple(
        region
        for region in [*anchor_regions, *rail_regions]
        if region.refs
    )
    return SheetCircuitRegions(regions=regions)


def _refs_by_net(
    nets: dict[str, list[ResolvedEndpoint]],
) -> dict[str, set[str]]:
    return {
        net_name: {
            endpoint.ref
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref is not None
        }
        for net_name, endpoints in nets.items()
    }


def _nets_by_ref(
    nets: dict[str, list[ResolvedEndpoint]],
) -> dict[str, set[str]]:
    nets_by_ref: dict[str, set[str]] = {}
    for net_name, endpoints in nets.items():
        for endpoint in endpoints:
            if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref is not None:
                nets_by_ref.setdefault(endpoint.ref, set()).add(net_name)
    return nets_by_ref


def _anchor_score(ref: str) -> tuple[int, str]:
    prefix = symbol_prefix(ref)
    if ref.startswith("Module") or prefix in {"U", "IC"}:
        return (0, ref)
    if prefix == "Q":
        return (1, ref)
    if prefix in {"L", "FB"}:
        return (2, ref)
    if prefix == "F":
        return (3, ref)
    if prefix in {"J", "P", "CN"}:
        return (4, ref)
    return (5, ref)

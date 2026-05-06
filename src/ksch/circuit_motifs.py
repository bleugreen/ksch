from dataclasses import dataclass
from typing import Literal

from ksch.geometry import is_vertical_two_pin_symbol
from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedEndpoint, ResolvedProject

TwoPinMotifKind = Literal["series_path", "shunt", "clamp", "two_pin"]


@dataclass(frozen=True)
class TwoPinMotif:
    ref: str
    kind: TwoPinMotifKind
    nets: tuple[str, str]
    ground_net: str | None = None
    other_net: str | None = None


@dataclass(frozen=True)
class TapStackMotif:
    anchor_ref: str
    anchor_pin_name: str
    anchor_pin_number: str
    tap_net: str
    top_ref: str
    bottom_ref: str
    top_net: str
    bottom_net: str

    @property
    def refs(self) -> tuple[str, str]:
        return (self.top_ref, self.bottom_ref)


@dataclass(frozen=True)
class RailBankMotif:
    top_net: str
    bottom_net: str
    refs: tuple[str, ...]


@dataclass(frozen=True)
class SheetCircuitMotifs:
    two_pin_refs: tuple[TwoPinMotif, ...]
    tap_stacks: tuple[TapStackMotif, ...]
    rail_banks: tuple[RailBankMotif, ...]

    def tap_stack_for_anchor_pin(
        self,
        anchor_ref: str,
        pin_name: str,
        pin_number: str,
    ) -> TapStackMotif | None:
        return next(
            (
                motif
                for motif in self.tap_stacks
                if motif.anchor_ref == anchor_ref
                and motif.anchor_pin_name == pin_name
                and motif.anchor_pin_number == pin_number
            ),
            None,
        )

    def tap_stack_refs(self) -> set[str]:
        return {ref for motif in self.tap_stacks for ref in motif.refs}


def symbol_prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def is_anchor_ref(ref: str) -> bool:
    prefix = symbol_prefix(ref)
    return prefix in {"J", "P", "CN", "U", "IC", "Q", "F", "L", "FB"} or ref.startswith(
        "Module"
    )


def is_groundish_net(name: str) -> bool:
    upper = name.upper()
    return upper == "GND" or "GND" in upper


def build_sheet_circuit_motifs(
    project: ResolvedProject,
    sheet_path: str,
) -> SheetCircuitMotifs:
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return SheetCircuitMotifs(two_pin_refs=(), tap_stacks=(), rail_banks=())

    two_pin_refs = _two_pin_ref_motifs(project, sheet_path)
    two_pin_ref_names = {motif.ref for motif in two_pin_refs}
    nets_by_ref = _nets_by_ref(resolved_sheet.nets, two_pin_ref_names)
    return SheetCircuitMotifs(
        two_pin_refs=tuple(sorted(two_pin_refs, key=lambda motif: motif.ref)),
        tap_stacks=tuple(
            sorted(
                _tap_stack_motifs(resolved_sheet.nets, nets_by_ref, two_pin_ref_names),
                key=lambda motif: (
                    motif.anchor_ref,
                    motif.tap_net,
                    motif.top_ref,
                    motif.bottom_ref,
                ),
            )
        ),
        rail_banks=tuple(
            sorted(
                _rail_bank_motifs(two_pin_refs),
                key=lambda motif: (motif.top_net, motif.bottom_net, motif.refs),
            )
        ),
    )


def _two_pin_ref_motifs(project: ResolvedProject, sheet_path: str) -> list[TwoPinMotif]:
    sheet = project.source.sheets[sheet_path]
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return []
    nets_by_ref = _nets_by_ref(resolved_sheet.nets, set(sheet.symbols))
    motifs: list[TwoPinMotif] = []
    for ref, symbol_decl in sheet.symbols.items():
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        if not is_vertical_two_pin_symbol(symbol_info):
            continue
        net_names = tuple(sorted(nets_by_ref.get(ref, set())))
        if len(net_names) != 2:
            continue
        ground_nets = tuple(net for net in net_names if is_groundish_net(net))
        non_ground_nets = tuple(net for net in net_names if not is_groundish_net(net))
        if ground_nets and non_ground_nets:
            kind: TwoPinMotifKind = "clamp" if symbol_prefix(ref) == "D" else "shunt"
            motifs.append(
                TwoPinMotif(
                    ref=ref,
                    kind=kind,
                    nets=net_names,
                    ground_net=ground_nets[0],
                    other_net=non_ground_nets[0],
                )
            )
            continue
        motifs.append(
            TwoPinMotif(
                ref=ref,
                kind="series_path" if not ground_nets else "two_pin",
                nets=net_names,
            )
        )
    return motifs


def _nets_by_ref(
    nets: dict[str, list[ResolvedEndpoint]],
    refs: set[str],
) -> dict[str, set[str]]:
    nets_by_ref: dict[str, set[str]] = {ref: set() for ref in refs}
    for net_name, endpoints in nets.items():
        for endpoint in endpoints:
            if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref in refs:
                nets_by_ref[endpoint.ref or ""].add(net_name)
    return nets_by_ref


def _tap_stack_motifs(
    nets: dict[str, list[ResolvedEndpoint]],
    nets_by_ref: dict[str, set[str]],
    two_pin_refs: set[str],
) -> list[TapStackMotif]:
    motifs: list[TapStackMotif] = []
    for tap_net, endpoints in nets.items():
        if is_groundish_net(tap_net):
            continue
        anchor_endpoints = [
            endpoint
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN
            and endpoint.ref is not None
            and is_anchor_ref(endpoint.ref)
        ]
        passive_refs = sorted(
            {
                endpoint.ref or ""
                for endpoint in endpoints
                if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref in two_pin_refs
            }
        )
        if not anchor_endpoints or len(passive_refs) != 2:
            continue
        first_ref, second_ref = passive_refs
        first_other_nets = nets_by_ref.get(first_ref, set()) - {tap_net}
        second_other_nets = nets_by_ref.get(second_ref, set()) - {tap_net}
        first_ground = next(
            (net for net in sorted(first_other_nets) if is_groundish_net(net)),
            None,
        )
        second_ground = next(
            (net for net in sorted(second_other_nets) if is_groundish_net(net)),
            None,
        )
        if (first_ground is None) == (second_ground is None):
            continue
        if first_ground is None:
            if second_ground is None or not first_other_nets:
                continue
            top_ref = first_ref
            bottom_ref = second_ref
            top_net = sorted(first_other_nets)[0]
            bottom_net = second_ground
        else:
            if not second_other_nets:
                continue
            top_ref = second_ref
            bottom_ref = first_ref
            top_net = sorted(second_other_nets)[0]
            bottom_net = first_ground
        anchor = sorted(
            anchor_endpoints,
            key=lambda endpoint: (symbol_prefix(endpoint.ref or ""), endpoint.ref or ""),
        )[0]
        if anchor.ref is None or anchor.pin_name is None or anchor.pin_number is None:
            continue
        motifs.append(
            TapStackMotif(
                anchor_ref=anchor.ref,
                anchor_pin_name=anchor.pin_name,
                anchor_pin_number=anchor.pin_number,
                tap_net=tap_net,
                top_ref=top_ref,
                bottom_ref=bottom_ref,
                top_net=top_net,
                bottom_net=bottom_net,
            )
        )
    return motifs


def _rail_bank_motifs(two_pin_refs: list[TwoPinMotif]) -> list[RailBankMotif]:
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for motif in two_pin_refs:
        if motif.kind != "shunt" or motif.ground_net is None or motif.other_net is None:
            continue
        prefix = symbol_prefix(motif.ref)
        if prefix != "C":
            continue
        grouped.setdefault((prefix, motif.other_net, motif.ground_net), []).append(motif.ref)
    return [
        RailBankMotif(
            top_net=top_net,
            bottom_net=bottom_net,
            refs=tuple(sorted(refs)),
        )
        for (_prefix, top_net, bottom_net), refs in grouped.items()
        if len(refs) >= 2
    ]

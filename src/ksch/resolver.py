from dataclasses import dataclass, field

from ksch.errors import KschError
from ksch.kicad.footprints import FootprintInfo
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.model.ir import ProjectIR, SheetIR


@dataclass(frozen=True)
class LibraryContext:
    symbols: dict[str, SymbolInfo]
    footprints: dict[str, FootprintInfo]


@dataclass(frozen=True)
class ResolvedEndpoint:
    text: str
    kind: EndpointKind
    sheet_path: str
    ref: str | None = None
    pin_name: str | None = None
    pin_number: str | None = None
    child_sheet: str | None = None
    port: str | None = None


@dataclass
class ResolvedSheet:
    path: str
    nets: dict[str, list[ResolvedEndpoint]] = field(default_factory=dict)


@dataclass
class ResolvedProject:
    name: str
    source: ProjectIR
    symbol_library: dict[str, SymbolInfo] = field(default_factory=dict)
    sheets: dict[str, ResolvedSheet] = field(default_factory=dict)


def _matching_pins(symbol: SymbolInfo, pin_name: str) -> list[SymbolPin]:
    return [pin for pin in symbol.pins if pin.name == pin_name or pin.number == pin_name]


def _resolve_symbol_pin(
    sheet_path: str,
    ref: str,
    endpoint_text: str,
    symbol: SymbolInfo,
    pin_name: str,
    pin_number: str | None,
    all_matching: bool,
) -> list[ResolvedEndpoint]:
    matches = _matching_pins(symbol, pin_name)
    if pin_number is not None:
        matches = [pin for pin in matches if pin.number == pin_number]
        if not matches:
            raise KschError(f"{endpoint_text} does not match any pin on {symbol.lib_id}")
    elif all_matching:
        if not matches:
            raise KschError(f"{endpoint_text} does not match any pin on {symbol.lib_id}")
    elif len(matches) != 1:
        if matches:
            rendered = ", ".join(f"{ref}.{pin.name}@{pin.number}" for pin in matches)
            raise KschError(f"{endpoint_text} is ambiguous; matches: {rendered}")
        raise KschError(f"{endpoint_text} does not match any pin on {symbol.lib_id}")

    return [
        ResolvedEndpoint(
            text=f"{ref}.{pin.name}@{pin.number}" if all_matching else endpoint_text,
            kind=EndpointKind.SYMBOL_PIN,
            sheet_path=sheet_path,
            ref=ref,
            pin_name=pin.name,
            pin_number=pin.number,
        )
        for pin in matches
    ]


def _resolve_sheet_symbol_endpoint(
    sheet_path: str,
    sheet: SheetIR,
    endpoint_text: str,
    libraries: LibraryContext,
) -> list[ResolvedEndpoint]:
    endpoint = parse_endpoint(endpoint_text)
    if endpoint.kind is not EndpointKind.SYMBOL_PIN:
        raise KschError(f"{endpoint_text} is not a symbol pin endpoint")
    ref = endpoint.ref or ""
    symbol_decl = sheet.symbols.get(ref)
    if symbol_decl is None:
        raise KschError(f"unknown symbol reference {ref} in {sheet_path}")
    symbol = libraries.symbols.get(symbol_decl.lib)
    if symbol is None:
        raise KschError(f"unknown symbol library id {symbol_decl.lib}")
    return _resolve_symbol_pin(
        sheet_path,
        ref,
        endpoint_text,
        symbol,
        endpoint.pin_name or "",
        endpoint.pin_number,
        endpoint.all_matching,
    )


def resolved_endpoint_key(endpoint: ResolvedEndpoint) -> tuple[str, ...]:
    if endpoint.kind is EndpointKind.SYMBOL_PIN:
        return (
            endpoint.sheet_path,
            "symbol",
            endpoint.ref or "",
            endpoint.pin_number or "",
        )
    return (
        endpoint.sheet_path,
        "sheet_port",
        endpoint.child_sheet or "",
        endpoint.port or "",
    )


def resolve_endpoint_text(
    project: ProjectIR,
    sheet_path: str,
    endpoint_text: str,
    libraries: LibraryContext,
) -> list[ResolvedEndpoint]:
    sheet = project.sheets[sheet_path]
    endpoint = parse_endpoint(endpoint_text)
    if endpoint.kind is EndpointKind.SHEET_PORT:
        child = sheet.child_instances.get(endpoint.sheet or "")
        if child is None:
            raise KschError(f"unknown child sheet {endpoint.sheet}")
        child_sheet = project.sheets[child.target_path]
        if endpoint.port not in child_sheet.interface:
            raise KschError(f"unknown sheet port {endpoint_text}")
        return [
            ResolvedEndpoint(
                text=endpoint_text,
                kind=EndpointKind.SHEET_PORT,
                sheet_path=sheet_path,
                child_sheet=endpoint.sheet,
                port=endpoint.port,
            )
        ]

    return _resolve_sheet_symbol_endpoint(sheet_path, sheet, endpoint_text, libraries)


def resolve_project(
    project: ProjectIR,
    libraries: LibraryContext,
    *,
    validate_declared_symbols: bool = False,
) -> ResolvedProject:
    resolved = ResolvedProject(name=project.name, source=project, symbol_library=libraries.symbols)
    for sheet_path, sheet in project.sheets.items():
        resolved_sheet = ResolvedSheet(path=sheet_path)
        endpoint_nets: dict[tuple[str, ...], str] = {}
        if validate_declared_symbols:
            for ref, symbol_decl in sheet.symbols.items():
                if symbol_decl.lib not in libraries.symbols:
                    raise KschError(
                        f"{sheet.source_path}: symbols.{ref}.lib: "
                        f"unknown symbol library id {symbol_decl.lib}"
                    )
        for net_name, endpoint_texts in sheet.nets.items():
            resolved_endpoints: list[ResolvedEndpoint] = []
            for index, endpoint_text in enumerate(endpoint_texts):
                try:
                    resolved_endpoints.extend(
                        resolve_endpoint_text(project, sheet_path, endpoint_text, libraries)
                    )
                except (KschError, ValueError) as exc:
                    raise KschError(
                        f"{sheet.source_path}: nets.{net_name}[{index}]: {exc}"
                    ) from exc
            for resolved_endpoint in resolved_endpoints:
                endpoint_key = resolved_endpoint_key(resolved_endpoint)
                existing_net = endpoint_nets.get(endpoint_key)
                if existing_net is not None and existing_net != net_name:
                    raise KschError(
                        f"{resolved_endpoint.text} is connected to both "
                        f"{existing_net} and {net_name} in {sheet_path}"
                    )
                endpoint_nets[endpoint_key] = net_name
            resolved_sheet.nets[net_name] = resolved_endpoints
        for index, endpoint_text in enumerate(sheet.no_connects):
            try:
                for resolved_endpoint in _resolve_sheet_symbol_endpoint(
                    sheet_path,
                    sheet,
                    endpoint_text,
                    libraries,
                ):
                    existing_net = endpoint_nets.get(resolved_endpoint_key(resolved_endpoint))
                    if existing_net is not None:
                        raise KschError(
                            f"{resolved_endpoint.text} is connected to "
                            f"{existing_net} in {sheet_path}"
                        )
            except (KschError, ValueError) as exc:
                raise KschError(
                    f"{sheet.source_path}: no_connects[{index}]: {exc}"
                ) from exc
        resolved.sheets[sheet_path] = resolved_sheet
    return resolved

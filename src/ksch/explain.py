from pathlib import Path

from ksch.authoring import load_symbol_library_paths
from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.kicad.symbols import SymbolInfo
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.model.ir import ProjectIR, SheetIR
from ksch.model.source import SymbolDecl
from ksch.project_context import load_project_context


def explain_symbol_lines(symbol: SymbolInfo) -> list[str]:
    lines = [f"symbol {symbol.lib_id}"]
    if symbol.footprint:
        lines.append(f"footprint: {symbol.footprint}")
    for pin in sorted(symbol.pins, key=lambda item: (item.unit, item.number, item.name)):
        unit = f" unit {pin.unit}" if pin.unit != 1 else ""
        lines.append(f"{pin.name}@{pin.number} {pin.electrical_type}{unit}")
    return lines


def explain_project_target_lines(
    target: str,
    config: Path,
    symbol_library: list[str] | None,
) -> list[str]:
    context = load_project_context(config, symbol_library=symbol_library, require_config=True)
    assert context.config is not None
    project = load_project_ir(context.config.schema)
    symbols = load_symbol_library_paths(context.symbol_libraries)
    try:
        endpoint = parse_endpoint(target)
    except ValueError:
        endpoint = None

    ref = (
        endpoint.ref
        if endpoint is not None and endpoint.kind is EndpointKind.SYMBOL_PIN
        else target
    )
    if ref is None or "." in ref:
        raise KschError(f"cannot explain {target}; use REF or REF.PIN")
    found = _find_project_symbol(project, ref)
    if found is None:
        raise KschError(f"unknown symbol reference {ref}")
    sheet_path, _sheet, symbol_decl = found
    symbol = symbols.get(symbol_decl.lib)
    if symbol is None:
        raise KschError(f"unknown symbol library id {symbol_decl.lib}")

    lines = [
        f"ref {ref}",
        f"sheet: {sheet_path}",
        f"lib: {symbol_decl.lib}",
    ]
    if symbol_decl.value:
        lines.append(f"value: {symbol_decl.value}")
    if symbol_decl.footprint:
        lines.append(f"footprint: {symbol_decl.footprint}")
    if endpoint is not None and endpoint.kind is EndpointKind.SYMBOL_PIN:
        pin_lines = _matching_symbol_pins(
            symbol,
            endpoint.pin_name or "",
            endpoint.pin_number,
        )
        if not pin_lines:
            raise KschError(f"{target} does not match any pin on {symbol.lib_id}")
        lines.extend(pin_lines)
    else:
        lines.extend(explain_symbol_lines(symbol)[1:])
    return lines


def _find_project_symbol(project: ProjectIR, ref: str) -> tuple[str, SheetIR, SymbolDecl] | None:
    for sheet_path, sheet in project.sheets.items():
        symbol = sheet.symbols.get(ref)
        if symbol is not None:
            return sheet_path, sheet, symbol
    return None


def _matching_symbol_pins(
    symbol: SymbolInfo,
    pin_name: str,
    pin_number: str | None = None,
) -> list[str]:
    pins = [
        pin
        for pin in symbol.pins
        if pin.name == pin_name or pin.number == pin_name
    ]
    if pin_number is not None:
        pins = [pin for pin in pins if pin.number == pin_number]
    return [
        f"{pin.name}@{pin.number} {pin.electrical_type}"
        for pin in sorted(pins, key=lambda item: (item.unit, item.number, item.name))
    ]

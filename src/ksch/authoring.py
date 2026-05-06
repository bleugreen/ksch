from pathlib import Path

from ksch.kicad.symbols import SymbolInfo, SymbolLibraryIndex, index_symbol_library


def parse_symbol_library_spec(value: str) -> tuple[str, Path]:
    nickname, separator, path_text = value.partition("=")
    if not separator or not nickname or not path_text:
        raise ValueError("expected NICKNAME=PATH")
    return nickname, Path(path_text)


def parse_symbol_library_specs(
    library_specs: list[str],
    *,
    base_dir: Path | None = None,
) -> dict[str, Path]:
    libraries: dict[str, Path] = {}
    for spec in library_specs:
        nickname, path = parse_symbol_library_spec(spec)
        if base_dir is not None and not path.is_absolute():
            path = base_dir / path
        libraries[nickname] = path
    return libraries


def index_symbol_library_paths(libraries: dict[str, Path]) -> dict[str, SymbolLibraryIndex]:
    return {
        nickname: index_symbol_library(nickname, path)
        for nickname, path in libraries.items()
    }


def index_symbol_libraries(library_specs: list[str]) -> dict[str, SymbolLibraryIndex]:
    return index_symbol_library_paths(parse_symbol_library_specs(library_specs))


def load_symbol_libraries(library_specs: list[str]) -> dict[str, SymbolInfo]:
    return load_symbol_library_paths(parse_symbol_library_specs(library_specs))


def load_symbol_library_paths(libraries: dict[str, Path]) -> dict[str, SymbolInfo]:
    symbols: dict[str, SymbolInfo] = {}
    for index in index_symbol_library_paths(libraries).values():
        symbols.update(index.symbols)
    return symbols


def symbol_info_lines(symbol: SymbolInfo) -> list[str]:
    lines = [symbol.lib_id]
    if symbol.footprint:
        lines.append(f"footprint: {symbol.footprint}")
    for pin in symbol.pins:
        lines.append(f"{pin.name}@{pin.number} {pin.electrical_type}")
    return lines

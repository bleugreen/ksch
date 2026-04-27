from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console

from ksch import __version__
from ksch.emit import write_project
from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.kicad.symbols import SymbolInfo, SymbolLibraryIndex, index_symbol_library
from ksch.resolver import LibraryContext, resolve_project
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_file

app = typer.Typer(
    add_completion=False,
    help="Canonical text-first schematic compiler for KiCad.",
    no_args_is_help=True,
)
symbol_app = typer.Typer(help="Inspect KiCad symbol libraries.", no_args_is_help=True)
symbols_app = typer.Typer(help="Search KiCad symbol libraries.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ksch {__version__}")
        raise typer.Exit()


def _exit_error(message: str) -> NoReturn:
    err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(1)


def _format_error(exc: Exception) -> str:
    if isinstance(exc, KschError):
        return str(exc)
    if isinstance(exc, ValidationError):
        return str(exc)
    return str(exc)


def _load_and_validate_project(path: Path) -> None:
    load_yaml_file(path)
    load_project_ir(path)


def _parse_library(value: str) -> tuple[str, Path]:
    nickname, separator, path_text = value.partition("=")
    if not separator or not nickname or not path_text:
        raise typer.BadParameter("expected NICKNAME=PATH")
    return nickname, Path(path_text)


def _index_libraries(library_specs: list[str]) -> dict[str, SymbolLibraryIndex]:
    indexes: dict[str, SymbolLibraryIndex] = {}
    for spec in library_specs:
        nickname, path = _parse_library(spec)
        indexes[nickname] = index_symbol_library(nickname, path)
    return indexes


def _load_symbol_libraries(library_specs: list[str]) -> dict[str, SymbolInfo]:
    symbols: dict[str, SymbolInfo] = {}
    for index in _index_libraries(library_specs).values():
        symbols.update(index.symbols)
    return symbols


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            help="Show the version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Canonical text-first schematic compiler for KiCad."""


@app.command()
def validate(path: Annotated[Path, typer.Argument(help="Root .ksch.yaml project file.")]) -> None:
    """Validate a project and all referenced sheets."""
    try:
        _load_and_validate_project(path)
    except (KschError, ValidationError, ValueError) as exc:
        _exit_error(_format_error(exc))
    console.print(f"{path} valid")


@app.command("fmt")
def fmt_command(
    path: Annotated[Path, typer.Argument(help="Schema file to format.")],
    check: Annotated[
        bool,
        typer.Option("--check", help="Exit with status 1 if the file is not formatted."),
    ] = False,
) -> None:
    """Format a schema file."""
    try:
        original = path.read_text(encoding="utf-8")
        formatted = format_schema_text(original, path)
    except (KschError, ValidationError, OSError) as exc:
        _exit_error(_format_error(exc))

    if check:
        if original != formatted:
            console.print(f"{path} would be reformatted")
            raise typer.Exit(1)
        console.print(f"{path} already formatted")
        return

    if original != formatted:
        path.write_text(formatted, encoding="utf-8")
    console.print(f"{path} formatted")


@app.command()
def expand(path: Annotated[Path, typer.Argument(help="Root .ksch.yaml project file.")]) -> None:
    """Print the expanded project sheet paths."""
    try:
        project = load_project_ir(path)
    except (KschError, ValidationError, ValueError) as exc:
        _exit_error(_format_error(exc))

    for sheet_path in sorted(project.sheets):
        console.print(sheet_path)


@app.command("compile")
def compile_command(
    path: Annotated[Path, typer.Argument(help="Root .ksch.yaml project file.")],
    out: Annotated[Path, typer.Option("--out", help="Output KiCad project directory.")],
    symbol_library: Annotated[
        list[str] | None,
        typer.Option("--symbol-library", help="Symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Compile a schema project into KiCad project files."""
    try:
        project = load_project_ir(path)
        symbols = _load_symbol_libraries(symbol_library or [])
        resolved = resolve_project(project, LibraryContext(symbols=symbols, footprints={}))
        write_project(resolved, out)
    except (KschError, ValidationError, ValueError, OSError) as exc:
        _exit_error(_format_error(exc))

    console.print(f"wrote {out}")


@symbols_app.command("search")
def symbols_search(
    query: Annotated[str, typer.Argument(help="Case-insensitive symbol search query.")],
    library: Annotated[
        list[str] | None,
        typer.Option("--library", "-L", help="Symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Search indexed symbols by library id."""
    try:
        symbols = _load_symbol_libraries(library or [])
    except (KschError, OSError) as exc:
        _exit_error(_format_error(exc))

    query_lower = query.lower()
    for lib_id in sorted(symbols):
        if query_lower in lib_id.lower():
            console.print(lib_id)


@app.command("pin-search")
def pin_search(
    symbol_id: Annotated[str, typer.Argument(help="Symbol library id, such as Device:R.")],
    query: Annotated[str, typer.Argument(help="Case-insensitive pin name or number query.")],
    library: Annotated[
        list[str] | None,
        typer.Option("--library", "-L", help="Symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Search pins on one symbol."""
    try:
        symbols = _load_symbol_libraries(library or [])
    except (KschError, OSError) as exc:
        _exit_error(_format_error(exc))

    symbol = symbols.get(symbol_id)
    if symbol is None:
        _exit_error(f"symbol '{symbol_id}' not found")

    query_lower = query.lower()
    for pin in symbol.pins:
        if query_lower in pin.name.lower() or query_lower in pin.number.lower():
            console.print(f"{pin.name}@{pin.number} {pin.electrical_type}")


@symbol_app.command("info")
def symbol_info(
    lib_id: Annotated[str, typer.Argument(help="Symbol library id, such as Device:R.")],
    library: Annotated[
        list[str] | None,
        typer.Option("--library", "-L", help="Symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Print indexed symbol information."""
    try:
        indexes = _index_libraries(library or [])
    except (KschError, OSError) as exc:
        _exit_error(_format_error(exc))

    nickname, separator, _symbol_name = lib_id.partition(":")
    if not separator:
        _exit_error("symbol id must use NICKNAME:SYMBOL")
    if nickname not in indexes:
        _exit_error(f"library '{nickname}' was not provided")

    symbol = indexes[nickname].symbols.get(lib_id)
    if symbol is None:
        _exit_error(f"symbol '{lib_id}' not found")

    console.print(symbol.lib_id)
    if symbol.footprint:
        console.print(f"footprint: {symbol.footprint}")
    for pin in symbol.pins:
        console.print(f"{pin.name}@{pin.number} {pin.electrical_type}")


app.add_typer(symbol_app, name="symbol")
app.add_typer(symbols_app, name="symbols")

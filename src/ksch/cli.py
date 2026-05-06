import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console

from ksch import __version__
from ksch.authoring import index_symbol_libraries, load_symbol_libraries, symbol_info_lines
from ksch.compiler import write_project
from ksch.config import ProjectConfig, load_project_config
from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.importer import import_project
from ksch.kicad.symbols import index_symbol_library
from ksch.resolver import LibraryContext, ResolvedProject, resolve_project
from ksch.scaffold import create_project_from_kicad, create_starter_project, discover_kicad_roots
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_file
from ksch.verify import compare_dirs

app = typer.Typer(
    add_completion=False,
    help="Canonical text-first schematic compiler for KiCad.",
    no_args_is_help=True,
)
symbol_app = typer.Typer(help="Inspect KiCad symbol libraries.", no_args_is_help=True)
symbols_app = typer.Typer(help="Search KiCad symbol libraries.", no_args_is_help=True)
skill_app = typer.Typer(help="Print bundled Codex skill material.", no_args_is_help=True)
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


def _resolved_project_context(
    path: Path,
    symbol_library: list[str],
    *,
    validate_declared_symbols: bool = False,
) -> tuple[ResolvedProject, dict[str, Path]]:
    project = load_project_ir(path)
    indexes = index_symbol_libraries(symbol_library)
    for nickname, library_path in project.symbol_libraries.items():
        if nickname not in indexes:
            indexes[nickname] = index_symbol_library(nickname, library_path)
    symbols = {}
    symbol_libraries = {}
    for nickname, index in indexes.items():
        symbols.update(index.symbols)
        symbol_libraries[nickname] = index.path
    resolved = resolve_project(
        project,
        LibraryContext(symbols=symbols, footprints={}),
        validate_declared_symbols=validate_declared_symbols,
    )
    return resolved, symbol_libraries


def _load_and_validate_project(path: Path, symbol_library: list[str]) -> None:
    load_yaml_file(path)
    _resolved_project_context(path, symbol_library, validate_declared_symbols=True)


def _compile_project(path: Path, out: Path, symbol_library: list[str]) -> None:
    resolved, symbol_libraries = _resolved_project_context(path, symbol_library)
    write_project(
        resolved,
        out,
        symbol_libraries=symbol_libraries,
        footprint_libraries=resolved.source.footprint_libraries,
    )


def _load_config_for_defaults(
    config: Path,
    *,
    need_schema: bool,
    need_out: bool,
) -> ProjectConfig | None:
    if not need_schema and not need_out:
        return None
    return load_project_config(config)


def _configured_symbol_libraries(
    config: ProjectConfig | None,
    symbol_library: list[str] | None,
) -> list[str]:
    return [*(config.symbol_library if config is not None else ()), *(symbol_library or [])]


def _skill_text() -> str:
    return files("ksch.skills.ksch").joinpath("SKILL.md").read_text(encoding="utf-8")


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


@app.command("init")
def init_command(
    path: Annotated[
        Path,
        typer.Argument(help="Project directory. Defaults to the current directory."),
    ] = Path("."),
    name: Annotated[
        str | None,
        typer.Option("--name", help="KiCad project name. Defaults to the directory name."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow writing into an existing non-empty directory."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Accept importing a detected KiCad schematic."),
    ] = False,
    blank: Annotated[
        bool,
        typer.Option(
            "--blank",
            help="Create a starter project even if KiCad schematics are found.",
        ),
    ] = False,
) -> None:
    """Create a starter schema project or import an existing KiCad schematic project."""
    try:
        if not blank:
            candidates = discover_kicad_roots(path)
            if len(candidates) == 1:
                root_schematic = candidates[0]
                prompt = f"Found KiCad schematic {root_schematic}. Import it into ksch?"
                if yes or typer.confirm(prompt, default=True):
                    imported = create_project_from_kicad(
                        path,
                        root_schematic=root_schematic,
                        force=force,
                    )
                    console.print(f"wrote {imported.root_schema}")
                    return
            elif len(candidates) > 1:
                rendered = "\n".join(f"- {candidate}" for candidate in candidates)
                raise KschError(
                    "multiple KiCad schematics found; run ksch init from one project directory:\n"
                    f"{rendered}"
                )
        create_starter_project(path, project_name=name, force=force)
    except (KschError, OSError) as exc:
        _exit_error(_format_error(exc))
    console.print(f"wrote {path}")


@app.command()
def validate(
    path: Annotated[Path, typer.Argument(help="Root .ksch.yaml project file.")],
    symbol_library: Annotated[
        list[str] | None,
        typer.Option("--symbol-library", help="Symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Validate a project and all referenced sheets."""
    try:
        _load_and_validate_project(path, symbol_library or [])
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
        _compile_project(path, out, symbol_library or [])
    except (KschError, ValidationError, ValueError, OSError) as exc:
        _exit_error(_format_error(exc))

    console.print(f"wrote {out}")


@app.command("gen")
def gen_command(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
    symbol_library: Annotated[
        list[str] | None,
        typer.Option("--symbol-library", help="Extra symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Generate the configured KiCad project from ksch.toml."""
    try:
        project_config = load_project_config(config)
        _compile_project(
            project_config.schema,
            project_config.out,
            _configured_symbol_libraries(project_config, symbol_library),
        )
    except (KschError, ValidationError, ValueError, OSError) as exc:
        _exit_error(_format_error(exc))

    console.print(f"wrote {project_config.out}")


@app.command("import")
def import_command(
    path: Annotated[Path, typer.Argument(help="Root .kicad_sch schematic file.")],
    out: Annotated[Path, typer.Option("--out", help="Output schema project directory.")],
) -> None:
    """Import a KiCad schematic project into schema files."""
    try:
        imported = import_project(path, out)
    except (KschError, ValidationError, ValueError, OSError, RuntimeError) as exc:
        _exit_error(_format_error(exc))

    console.print(f"wrote {imported.root_schema}")


@app.command("check")
def check_command(
    path: Annotated[
        Path | None,
        typer.Argument(help="Root .ksch.yaml project file. Defaults to ksch.toml schema."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Generated KiCad project directory. Defaults to ksch.toml out."),
    ] = None,
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
    symbol_library: Annotated[
        list[str] | None,
        typer.Option("--symbol-library", help="Extra symbol library as NICKNAME=PATH."),
    ] = None,
) -> None:
    """Regenerate a project and report drift from an output directory."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            project_config = _load_config_for_defaults(
                config,
                need_schema=path is None,
                need_out=out is None,
            )
            schema_path = path or project_config.schema if project_config is not None else path
            out_path = out or project_config.out if project_config is not None else out
            if schema_path is None:
                raise KschError("schema path is required when ksch.toml is not used")
            if out_path is None:
                raise KschError("--out is required when ksch.toml is not used")
            _compile_project(
                schema_path,
                tmp_path,
                _configured_symbol_libraries(project_config, symbol_library),
            )
            findings = compare_dirs(tmp_path, out_path)
        except (KschError, ValidationError, ValueError, OSError) as exc:
            _exit_error(_format_error(exc))

    if findings:
        for finding in findings:
            console.print(finding)
        raise typer.Exit(1)
    console.print("generated output matches schema")


@skill_app.command("show")
def skill_show() -> None:
    """Print the bundled Codex skill for ksch projects."""
    try:
        typer.echo(_skill_text(), nl=False)
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        _exit_error(_format_error(exc))


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
        symbols = load_symbol_libraries(library or [])
    except (KschError, OSError, ValueError) as exc:
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
        symbols = load_symbol_libraries(library or [])
    except (KschError, OSError, ValueError) as exc:
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
        symbols = load_symbol_libraries(library or [])
    except (KschError, OSError, ValueError) as exc:
        _exit_error(_format_error(exc))

    nickname, separator, _symbol_name = lib_id.partition(":")
    if not separator:
        _exit_error("symbol id must use NICKNAME:SYMBOL")
    if not any(symbol_id.startswith(f"{nickname}:") for symbol_id in symbols):
        _exit_error(f"library '{nickname}' was not provided")

    symbol = symbols.get(lib_id)
    if symbol is None:
        _exit_error(f"symbol '{lib_id}' not found")

    for line in symbol_info_lines(symbol):
        console.print(line)


app.add_typer(symbol_app, name="symbol")
app.add_typer(symbols_app, name="symbols")
app.add_typer(skill_app, name="skill")

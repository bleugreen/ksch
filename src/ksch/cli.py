import shutil
import tempfile
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console

from ksch import __version__
from ksch.authoring import (
    index_symbol_libraries,
    load_symbol_library_paths,
    parse_symbol_library_spec,
    symbol_info_lines,
)
from ksch.compiler import write_project
from ksch.config import ProjectConfig, load_project_config
from ksch.edit import add_symbol, connect_endpoints
from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.explain import explain_project_target_lines, explain_symbol_lines
from ksch.importer import ImportedProject, import_project
from ksch.kicad.symbols import SymbolInfo, index_symbol_library
from ksch.project_context import load_project_context
from ksch.resolver import LibraryContext, ResolvedProject, resolve_project
from ksch.scaffold import create_project_from_kicad, create_starter_project, discover_kicad_roots
from ksch.schema.formatter import format_schema_text
from ksch.schema.json_schema import schema_json_text
from ksch.schema.loader import load_yaml_file
from ksch.verify import (
    compare_dirs,
    compare_netlist_signatures,
    export_kicad_netlist,
    run_kicad_erc,
)

app = typer.Typer(
    add_completion=False,
    help="Canonical text-first schematic compiler for KiCad.",
    no_args_is_help=True,
)
symbol_app = typer.Typer(help="Inspect KiCad symbol libraries.", no_args_is_help=True)
symbols_app = typer.Typer(help="Search KiCad symbol libraries.", no_args_is_help=True)
skill_app = typer.Typer(help="Print bundled Codex skill material.", no_args_is_help=True)
schema_app = typer.Typer(help="Print schema authoring material.", no_args_is_help=True)
edit_app = typer.Typer(help="Apply structured schema edits.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True, soft_wrap=True)


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


def _schema_from_config_or_arg(config: Path, schema: Path | None) -> Path:
    if schema is not None:
        return schema
    return load_project_config(config).schema


def _configured_symbol_libraries(
    config: ProjectConfig | None,
    symbol_library: list[str] | None,
) -> list[str]:
    configured: list[str] = []
    if config is not None:
        for spec in config.symbol_library:
            nickname, path = parse_symbol_library_spec(spec)
            if not path.is_absolute():
                path = config.root / path
            configured.append(f"{nickname}={path}")
    return [*configured, *(symbol_library or [])]


def _skill_text() -> str:
    return files("ksch.skills.ksch").joinpath("SKILL.md").read_text(encoding="utf-8")


def _print_import_result(imported: ImportedProject) -> None:
    console.print(f"wrote {imported.root_schema}")
    child_sheets = sorted(path for path in imported.generated_files if path != imported.root_schema)
    if not child_sheets:
        return
    suffix = "" if len(child_sheets) == 1 else "s"
    console.print(f"wrote {len(child_sheets)} child sheet schema{suffix}:")
    for path in child_sheets:
        console.print(f"- {path}")


def _load_authoring_symbols(
    config: Path,
    symbol_library: list[str] | None,
) -> dict[str, SymbolInfo]:
    context = load_project_context(config, symbol_library=symbol_library)
    return load_symbol_library_paths(context.symbol_libraries)


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
                    _print_import_result(imported)
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


@schema_app.command("show")
def schema_show() -> None:
    """Print the canonical JSON Schema for .ksch.yaml documents."""
    console.print(schema_json_text(), end="", markup=False)


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

    _print_import_result(imported)


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


@app.command("verify")
def verify_command(
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
    against: Annotated[
        Path | None,
        typer.Option(
            "--against",
            help="Original KiCad root schematic to compare netlist connectivity against.",
        ),
    ] = None,
    artifacts: Annotated[
        Path | None,
        typer.Option("--artifacts", help="Directory for generated verification artifacts."),
    ] = None,
    no_erc: Annotated[
        bool,
        typer.Option("--no-erc", help="Skip KiCad ERC on the generated schematic."),
    ] = False,
    no_drift: Annotated[
        bool,
        typer.Option("--no-drift", help="Skip generated-file drift comparison against out."),
    ] = False,
) -> None:
    """Compile, ERC-check, and optionally netlist-compare a schema project."""
    temp_context = tempfile.TemporaryDirectory() if artifacts is None else None
    root = Path(temp_context.name) if temp_context is not None else artifacts
    assert root is not None
    try:
        generated_dir = root / "generated" if artifacts is not None else root
        generated_dir.mkdir(parents=True, exist_ok=True)
        project_config = _load_config_for_defaults(
            config,
            need_schema=path is None,
            need_out=out is None,
        )
        schema_path = path or project_config.schema if project_config is not None else path
        out_path = out or project_config.out if project_config is not None else out
        if schema_path is None:
            raise KschError("schema path is required when ksch.toml is not used")
        if out_path is None and not no_drift:
            raise KschError("--out is required for drift checks when ksch.toml is not used")

        resolved, symbol_libraries = _resolved_project_context(
            schema_path,
            _configured_symbol_libraries(project_config, symbol_library),
        )
        write_project(
            resolved,
            generated_dir,
            symbol_libraries=symbol_libraries,
            footprint_libraries=resolved.source.footprint_libraries,
        )
        console.print(f"compiled {schema_path} -> {generated_dir}")

        findings: list[str] = []
        generated_root = generated_dir / f"{resolved.name}.kicad_sch"
        if not no_erc:
            erc_report = root / "erc.rpt"
            erc = run_kicad_erc(generated_root, erc_report)
            console.print(f"erc: {erc.violations} violation(s)")
            if erc.violations:
                report_hint = (
                    f"report: {erc.report}"
                    if artifacts is not None
                    else "rerun with --artifacts DIR to keep erc.rpt"
                )
                findings.append(f"ERC found {erc.violations} violation(s); {report_hint}")
        if against is not None:
            reference_netlist = root / "reference.net"
            generated_netlist = root / "generated.net"
            export_kicad_netlist(against, reference_netlist)
            export_kicad_netlist(generated_root, generated_netlist)
            netlist_findings = compare_netlist_signatures(reference_netlist, generated_netlist)
            if netlist_findings:
                findings.extend(f"netlist: {finding}" for finding in netlist_findings)
            else:
                console.print(f"netlist: matches {against}")
        if not no_drift:
            assert out_path is not None
            drift_findings = compare_dirs(generated_dir, out_path)
            if drift_findings:
                findings.extend(f"drift: {finding}" for finding in drift_findings)
            else:
                console.print(f"drift: generated output matches {out_path}")
    except (KschError, ValidationError, ValueError, OSError, RuntimeError) as exc:
        if temp_context is not None:
            temp_context.cleanup()
        _exit_error(_format_error(exc))
    finally:
        if temp_context is not None:
            temp_context.cleanup()

    if findings:
        for finding in findings:
            console.print(finding)
        raise typer.Exit(1)
    console.print("verification passed")


@app.command("doctor")
def doctor_command(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Report local ksch, KiCad CLI, and project library readiness."""
    errors = 0
    warnings = 0

    def ok(message: str) -> None:
        console.print(f"ok: {message}")

    def warn(message: str) -> None:
        nonlocal warnings
        warnings += 1
        console.print(f"warning: {message}")

    def error(message: str) -> None:
        nonlocal errors
        errors += 1
        console.print(f"error: {message}")

    kicad_cli = shutil.which("kicad-cli")
    if kicad_cli is None:
        error("kicad-cli not found on PATH")
    else:
        ok(f"kicad-cli {kicad_cli}")

    try:
        context = load_project_context(config, require_config=True)
    except (KschError, ValidationError, ValueError, OSError) as exc:
        error(_format_error(exc))
        raise typer.Exit(1) from exc

    project_config = context.config
    assert project_config is not None
    ok(f"config {project_config.config_path}")
    ok(f"schema {project_config.schema}")
    if project_config.out.exists():
        ok(f"output {project_config.out}")
    else:
        warn(f"output directory does not exist yet: {project_config.out}")

    if context.symbol_libraries:
        found = _report_library_paths("symbol", context.symbol_libraries, error)
        ok(f"{found}/{len(context.symbol_libraries)} symbol libraries found")
    else:
        warn("no symbol libraries discovered")

    if context.footprint_libraries:
        found = _report_library_paths("footprint", context.footprint_libraries, error)
        ok(f"{found}/{len(context.footprint_libraries)} footprint libraries found")

    if errors:
        raise typer.Exit(1)
    suffix = "" if warnings == 0 else f" with {warnings} warning(s)"
    console.print(f"doctor passed{suffix}")


@app.command("explain")
def explain_command(
    target: Annotated[
        str,
        typer.Argument(help="Library symbol id, project ref, or project endpoint to explain."),
    ],
    library: Annotated[
        list[str] | None,
        typer.Option("--library", "-L", help="Extra symbol library as NICKNAME=PATH."),
    ] = None,
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Explain a library symbol, project symbol ref, or endpoint."""
    try:
        if ":" in target:
            symbols = _load_authoring_symbols(config, library)
            symbol = symbols.get(target)
            if symbol is None:
                raise KschError(f"symbol '{target}' not found")
            lines = explain_symbol_lines(symbol)
        else:
            lines = explain_project_target_lines(target, config, library)
    except (KschError, OSError, ValueError) as exc:
        _exit_error(_format_error(exc))
    for line in lines:
        console.print(line)


@edit_app.command("connect")
def edit_connect_command(
    net_name: Annotated[str, typer.Argument(help="Net name to connect endpoints to.")],
    endpoints: Annotated[
        list[str],
        typer.Argument(help="Endpoint(s) to add to the net."),
    ],
    sheet_path: Annotated[
        str,
        typer.Option("--sheet", help="Schema sheet path to edit."),
    ] = "/",
    schema: Annotated[
        Path | None,
        typer.Option(
            "--schema",
            help="Root .ksch.yaml project file. Defaults to ksch.toml schema.",
        ),
    ] = None,
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Connect endpoint(s) to a schema net."""
    try:
        schema_path = _schema_from_config_or_arg(config, schema)
        result = connect_endpoints(
            schema_path,
            sheet_path=sheet_path,
            net_name=net_name,
            endpoints=endpoints,
        )
    except (KschError, ValidationError, ValueError, OSError) as exc:
        _exit_error(_format_error(exc))

    if not result.changed:
        console.print(f"{net_name}: endpoints already connected")
        return
    suffix = "" if len(result.added_endpoints) == 1 else "s"
    console.print(
        f"connected {len(result.added_endpoints)} endpoint{suffix} "
        f"to {net_name} in {result.schema_path}"
    )


@edit_app.command("add-symbol")
def edit_add_symbol_command(
    ref: Annotated[str, typer.Argument(help="Symbol reference to add, such as U1.")],
    lib_id: Annotated[str, typer.Argument(help="KiCad library id, such as Device:R.")],
    value: Annotated[
        str | None,
        typer.Option("--value", help="Symbol value."),
    ] = None,
    footprint: Annotated[
        str | None,
        typer.Option("--footprint", help="Symbol footprint id."),
    ] = None,
    sheet_path: Annotated[
        str,
        typer.Option("--sheet", help="Schema sheet path to edit."),
    ] = "/",
    schema: Annotated[
        Path | None,
        typer.Option(
            "--schema",
            help="Root .ksch.yaml project file. Defaults to ksch.toml schema.",
        ),
    ] = None,
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Add a symbol declaration to a schema sheet."""
    try:
        schema_path = _schema_from_config_or_arg(config, schema)
        result = add_symbol(
            schema_path,
            sheet_path=sheet_path,
            ref=ref,
            lib_id=lib_id,
            value=value,
            footprint=footprint,
        )
    except (KschError, ValidationError, ValueError, OSError) as exc:
        _exit_error(_format_error(exc))
    console.print(f"added symbol {result.ref} to {result.schema_path}")


def _report_library_paths(
    kind: str,
    libraries: dict[str, Path],
    report_error: Callable[[str], None],
) -> int:
    found = 0
    for nickname, path in sorted(libraries.items()):
        if path.exists():
            found += 1
        else:
            report_error(f"missing {kind} library {nickname}: {path}")
    return found


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
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Search indexed symbols by library id."""
    try:
        symbols = _load_authoring_symbols(config, library)
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
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Search pins on one symbol."""
    try:
        symbols = _load_authoring_symbols(config, library)
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
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to ksch.toml or a project directory."),
    ] = Path("ksch.toml"),
) -> None:
    """Print indexed symbol information."""
    try:
        symbols = _load_authoring_symbols(config, library)
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
app.add_typer(schema_app, name="schema")
app.add_typer(edit_app, name="edit")

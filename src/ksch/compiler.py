from pathlib import Path

from ksch.emit import write_project as write_placed_project
from ksch.layout_solver import solve_sheet_layout
from ksch.placed import PlacedProject, PlacedSheet
from ksch.placed_normalize import normalize_placed_project
from ksch.resolver import ResolvedProject


def build_placed_project(
    project: ResolvedProject,
    *,
    strict_geometry: bool = True,
    layout_errors: list[str] | None = None,
) -> PlacedProject:
    del strict_geometry

    sheets: list[PlacedSheet] = []
    for sheet_path in sorted(project.source.sheets):
        state = solve_sheet_layout(project, sheet_path)
        if layout_errors is not None:
            layout_errors.extend(state.layout_errors)
        sheets.append(
            PlacedSheet(
                path=state.path,
                filename=state.filename,
                uuid=_sheet_uuid(state.path),
                paper=state.paper,
                lib_symbols=state.lib_symbols,
                items=state.items,
                instance_path=state.instance_path,
                page=state.page,
            )
        )
    return normalize_placed_project(PlacedProject(name=project.name, sheets=tuple(sheets)))


def write_project(
    project: ResolvedProject,
    output_dir: Path,
    symbol_libraries: dict[str, Path] | None = None,
    footprint_libraries: dict[str, Path] | None = None,
    *,
    strict_geometry: bool = True,
    layout_errors: list[str] | None = None,
) -> PlacedProject:
    placed_project = build_placed_project(
        project,
        strict_geometry=strict_geometry,
        layout_errors=layout_errors,
    )
    write_placed_project(
        placed_project,
        output_dir,
        symbol_libraries=symbol_libraries,
        footprint_libraries=footprint_libraries,
    )
    return placed_project


def _sheet_uuid(sheet_path: str) -> str:
    from ksch.ids import stable_uuid

    return stable_uuid(sheet_path)

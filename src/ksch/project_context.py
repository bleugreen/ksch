from dataclasses import dataclass, field
from pathlib import Path

from ksch.authoring import parse_symbol_library_specs
from ksch.config import ProjectConfig, load_project_config
from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.kicad.libraries import parse_library_table


@dataclass(frozen=True)
class ProjectContext:
    config: ProjectConfig | None
    symbol_libraries: dict[str, Path] = field(default_factory=dict)
    footprint_libraries: dict[str, Path] = field(default_factory=dict)


def load_project_context(
    config: Path = Path("ksch.toml"),
    *,
    symbol_library: list[str] | None = None,
    require_config: bool = False,
) -> ProjectContext:
    config_path = config / "ksch.toml" if config.is_dir() else config
    if not config_path.exists():
        if require_config:
            raise KschError(f"{config_path} not found; run ksch init or pass explicit paths")
        return ProjectContext(
            config=None,
            symbol_libraries=parse_symbol_library_specs(symbol_library or []),
        )

    project_config = load_project_config(config_path)
    symbol_libraries: dict[str, Path] = {}
    footprint_libraries: dict[str, Path] = {}

    if not project_config.schema.exists():
        raise KschError(f"schema file not found: {project_config.schema}")

    project = load_project_ir(project_config.schema)
    symbol_libraries.update(_table_libraries(project_config.out, "sym-lib-table"))
    footprint_libraries.update(_table_libraries(project_config.out, "fp-lib-table"))
    symbol_libraries.update(project.symbol_libraries)
    footprint_libraries.update(project.footprint_libraries)
    symbol_libraries.update(
        parse_symbol_library_specs(
            list(project_config.symbol_library),
            base_dir=project_config.root,
        )
    )
    symbol_libraries.update(parse_symbol_library_specs(symbol_library or []))

    return ProjectContext(
        config=project_config,
        symbol_libraries=symbol_libraries,
        footprint_libraries=footprint_libraries,
    )


def _table_libraries(project_dir: Path, table_name: str) -> dict[str, Path]:
    table = project_dir / table_name
    if not table.exists():
        return {}
    parsed = parse_library_table(table, {"KIPRJMOD": str(project_dir)})
    return {name: entry.path for name, entry in parsed.entries.items()}

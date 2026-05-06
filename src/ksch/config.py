import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksch.errors import KschError


@dataclass(frozen=True)
class ProjectConfig:
    config_path: Path
    root: Path
    schema: Path
    out: Path
    symbol_library: tuple[str, ...] = ()


def load_project_config(path: Path = Path("ksch.toml")) -> ProjectConfig:
    config_path = path / "ksch.toml" if path.is_dir() else path
    if not config_path.exists():
        raise KschError(f"{config_path} not found; run ksch init or pass explicit paths")
    if not config_path.is_file():
        raise KschError(f"{config_path} is not a file")

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise KschError(f"invalid {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise KschError(f"invalid {config_path}: root must be a table")

    root = config_path.resolve().parent
    schema = _required_path(raw, "schema", config_path, root)
    out = _required_path(raw, "out", config_path, root)
    symbol_library = _optional_string_list(raw, "symbol_library", config_path)

    return ProjectConfig(
        config_path=config_path.resolve(),
        root=root,
        schema=schema,
        out=out,
        symbol_library=tuple(symbol_library),
    )


def _required_path(data: dict[str, Any], key: str, config_path: Path, root: Path) -> Path:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise KschError(f"{config_path} must define {key} as a non-empty string")
    path = Path(value)
    return path if path.is_absolute() else root / path


def _optional_string_list(
    data: dict[str, Any],
    key: str,
    config_path: Path,
) -> list[str]:
    value = data.get(key, [])
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise KschError(f"{config_path} must define {key} as a string or list of strings")
    return value

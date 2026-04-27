from pathlib import Path
from typing import Any

from sexpdata import Symbol, loads  # type: ignore[import-untyped]

Sexpr = list[Any] | str | int | float | Symbol


def load_sexpr_file(path: Path) -> list[Any]:
    data: object = loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} did not contain a top-level s-expression list")
    return data


def atom(value: Any) -> str:
    if isinstance(value, Symbol):
        return str(value.value())
    return str(value)

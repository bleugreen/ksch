from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class FootprintInfo:
    footprint_id: str
    name: str
    path: Path
    pads: set[str] = field(default_factory=set)
    description: str | None = None
    tags: str | None = None


@dataclass(frozen=True)
class FootprintLibraryIndex:
    nickname: str
    path: Path
    footprints: dict[str, FootprintInfo]


def _field(expr: list[Any], token: str) -> str | None:
    for item in expr:
        if isinstance(item, list) and item and atom(item[0]) == token and len(item) >= 2:
            return atom(item[1])
    return None


def _pads(expr: list[Any]) -> set[str]:
    pads: set[str] = set()
    for item in expr:
        if isinstance(item, list) and item and atom(item[0]) == "pad":
            pad = atom(item[1])
            if pad:
                pads.add(pad)
    return pads


def index_footprint_library(nickname: str, path: Path) -> FootprintLibraryIndex:
    footprints: dict[str, FootprintInfo] = {}
    for mod_path in sorted(path.glob("*.kicad_mod")):
        expr = load_sexpr_file(mod_path)
        name = atom(expr[1])
        footprint_id = f"{nickname}:{name}"
        footprints[footprint_id] = FootprintInfo(
            footprint_id=footprint_id,
            name=name,
            path=mod_path,
            pads=_pads(expr),
            description=_field(expr, "descr"),
            tags=_field(expr, "tags"),
        )
    return FootprintLibraryIndex(nickname=nickname, path=path, footprints=footprints)

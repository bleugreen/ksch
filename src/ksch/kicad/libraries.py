import os
import re
from dataclasses import dataclass
from pathlib import Path

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class LibraryEntry:
    name: str
    type: str
    uri: str
    path: Path
    description: str


@dataclass(frozen=True)
class LibraryTable:
    kind: str
    entries: dict[str, LibraryEntry]


VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_uri(uri: str, variables: dict[str, str]) -> Path:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return variables.get(name, os.environ.get(name, match.group(0)))

    return Path(VAR_PATTERN.sub(replace, uri)).expanduser()


def parse_library_table(path: Path, variables: dict[str, str] | None = None) -> LibraryTable:
    variables = variables or {}
    expr = load_sexpr_file(path)
    kind = atom(expr[0])
    entries: dict[str, LibraryEntry] = {}
    for item in expr[1:]:
        if not isinstance(item, list) or atom(item[0]) != "lib":
            continue
        fields: dict[str, str] = {}
        for child in item[1:]:
            if isinstance(child, list) and len(child) >= 2:
                fields[atom(child[0])] = atom(child[1])
        name = fields["name"]
        uri = fields["uri"]
        entries[name] = LibraryEntry(
            name=name,
            type=fields.get("type", ""),
            uri=uri,
            path=_expand_uri(uri, variables),
            description=fields.get("descr", ""),
        )
    return LibraryTable(kind=kind, entries=entries)

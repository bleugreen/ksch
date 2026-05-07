from dataclasses import dataclass
from pathlib import Path

from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.kicad.symbols import SymbolInfo, index_symbol_library
from ksch.model.ir import ProjectIR, SheetIR
from ksch.resolver import (
    LibraryContext,
    ResolvedEndpoint,
    resolve_endpoint_text,
    resolve_project,
    resolved_endpoint_key,
)


@dataclass(frozen=True)
class ProjectGraph:
    source: ProjectIR
    symbols: dict[str, SymbolInfo]
    endpoint_nets: dict[tuple[str, ...], str]

    @classmethod
    def from_schema(cls, path: Path) -> "ProjectGraph":
        project = load_project_ir(path)
        symbols = _load_symbol_indexes(project)
        resolved = resolve_project(
            project,
            LibraryContext(symbols=symbols, footprints={}),
            validate_declared_symbols=True,
        )
        endpoint_nets: dict[tuple[str, ...], str] = {}
        for resolved_sheet in resolved.sheets.values():
            for net_name, endpoints in resolved_sheet.nets.items():
                for endpoint in endpoints:
                    endpoint_nets[resolved_endpoint_key(endpoint)] = net_name
        return cls(
            source=project,
            symbols=symbols,
            endpoint_nets=endpoint_nets,
        )

    def sheet(self, sheet_path: str) -> SheetIR | None:
        return self.source.sheets.get(sheet_path)

    def net_for_endpoint(self, sheet_path: str, endpoint: str) -> str | None:
        resolved = self.resolve_endpoint(sheet_path, endpoint)
        endpoint_nets = [
            self.endpoint_nets.get(resolved_endpoint_key(resolved_endpoint))
            for resolved_endpoint in resolved
        ]
        nets = {net_name for net_name in endpoint_nets if net_name is not None}
        if not nets:
            return None
        if len(nets) == 1:
            if any(net_name is None for net_name in endpoint_nets):
                return None
            return next(iter(nets))
        rendered = ", ".join(sorted(nets))
        raise KschError(f"{endpoint} resolves to endpoints on multiple nets: {rendered}")

    def resolve_endpoint(self, sheet_path: str, endpoint: str) -> list[ResolvedEndpoint]:
        if sheet_path not in self.source.sheets:
            raise KschError(f"unknown sheet {sheet_path}")
        return resolve_endpoint_text(
            self.source,
            sheet_path,
            endpoint,
            LibraryContext(symbols=self.symbols, footprints={}),
        )


def _load_symbol_indexes(project: ProjectIR) -> dict[str, SymbolInfo]:
    symbols: dict[str, SymbolInfo] = {}
    for nickname, path in project.symbol_libraries.items():
        symbols.update(index_symbol_library(nickname, path).symbols)
    return symbols

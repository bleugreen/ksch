from dataclasses import dataclass
from pathlib import Path

from ksch.expand import load_project_ir
from ksch.model.ir import ProjectIR, SheetIR


@dataclass(frozen=True)
class ProjectGraph:
    source: ProjectIR
    endpoint_nets: dict[tuple[str, str], str]

    @classmethod
    def from_schema(cls, path: Path) -> "ProjectGraph":
        project = load_project_ir(path)
        endpoint_nets: dict[tuple[str, str], str] = {}
        for sheet_path, sheet in project.sheets.items():
            for net_name, endpoints in sheet.nets.items():
                for endpoint in endpoints:
                    endpoint_nets[(sheet_path, endpoint)] = net_name
        return cls(source=project, endpoint_nets=endpoint_nets)

    def sheet(self, sheet_path: str) -> SheetIR | None:
        return self.source.sheets.get(sheet_path)

    def net_for_endpoint(self, sheet_path: str, endpoint: str) -> str | None:
        return self.endpoint_nets.get((sheet_path, endpoint))

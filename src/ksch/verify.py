import subprocess
from dataclasses import dataclass
from filecmp import dircmp
from pathlib import Path

from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedProject


@dataclass(frozen=True)
class NetlistNet:
    name: str
    connections: set[tuple[str, str]]


def compare_connectivity(project: ResolvedProject, exported: dict[str, NetlistNet]) -> list[str]:
    findings: list[str] = []
    for sheet in project.sheets.values():
        for net_name, endpoints in sheet.nets.items():
            exported_net = exported.get(net_name)
            exported_connections = exported_net.connections if exported_net else set()
            for endpoint in endpoints:
                if endpoint.kind is not EndpointKind.SYMBOL_PIN:
                    continue
                expected = (endpoint.ref or "", endpoint.pin_number or "")
                if expected not in exported_connections:
                    findings.append(f"{net_name} missing {expected[0]}.{expected[1]}")
    return findings


def compare_dirs(expected: Path, actual: Path) -> list[str]:
    comparison = dircmp(expected, actual)
    findings: list[str] = []

    def walk(cmp: dircmp[str], prefix: Path) -> None:
        for name in cmp.left_only:
            findings.append(f"missing generated file {(prefix / name).as_posix()}")
        for name in cmp.right_only:
            findings.append(f"unexpected generated file {(prefix / name).as_posix()}")
        for name in cmp.diff_files:
            findings.append(f"generated file differs {(prefix / name).as_posix()}")
        for name, child in cmp.subdirs.items():
            walk(child, prefix / name)

    walk(comparison, Path("."))
    return findings


def run_kicad_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kicad-cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )

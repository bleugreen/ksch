import subprocess
from dataclasses import dataclass
from filecmp import dircmp
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file
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


def parse_kicadsexpr_netlist(path: Path) -> dict[str, NetlistNet]:
    expr = load_sexpr_file(path)
    nets: dict[str, NetlistNet] = {}
    nets_expr = _first_child(expr, "nets")
    if nets_expr is None:
        return nets
    for net_expr in _children(nets_expr, "net"):
        name = _child_atom(net_expr, "name") or ""
        connections = set()
        for node_expr in _children(net_expr, "node"):
            ref = _child_atom(node_expr, "ref")
            pin = _child_atom(node_expr, "pin")
            if ref and pin:
                connections.add((ref, pin))
        nets[name] = NetlistNet(name=name, connections=connections)
    return nets


def connectivity_signature(nets: dict[str, NetlistNet]) -> set[frozenset[tuple[str, str]]]:
    return {frozenset(net.connections) for net in nets.values() if len(net.connections) > 1}


def _child_atom(expr: list[Any], name: str) -> str | None:
    child = _first_child(expr, name)
    if child is None or len(child) < 2:
        return None
    return atom(child[1])


def _first_child(expr: list[Any], name: str) -> list[Any] | None:
    for child in _children(expr, name):
        return child
    return None


def _children(expr: list[Any], name: str) -> list[list[Any]]:
    return [
        item
        for item in expr[1:]
        if isinstance(item, list) and item and atom(item[0]) == name
    ]


def run_kicad_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kicad-cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )

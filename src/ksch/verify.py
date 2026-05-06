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


@dataclass(frozen=True)
class ErcResult:
    violations: int
    stdout: str
    stderr: str
    report: Path


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


def compare_netlist_signatures(reference: Path, generated: Path) -> list[str]:
    reference_signature = connectivity_signature(parse_kicadsexpr_netlist(reference))
    generated_signature = connectivity_signature(parse_kicadsexpr_netlist(generated))
    if reference_signature == generated_signature:
        return []
    missing = sorted(reference_signature - generated_signature, key=lambda item: sorted(item))
    unexpected = sorted(generated_signature - reference_signature, key=lambda item: sorted(item))
    findings: list[str] = []
    for signature in missing:
        findings.append(f"missing net connectivity {_format_signature(signature)}")
    for signature in unexpected:
        findings.append(f"unexpected net connectivity {_format_signature(signature)}")
    return findings


def export_kicad_netlist(schematic: Path, target: Path) -> None:
    result = run_kicad_cli(
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "--output",
            str(target),
            str(schematic),
        ]
    )
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "kicad-cli netlist export failed"
        )
        raise RuntimeError(message)


def run_kicad_erc(schematic: Path, report: Path) -> ErcResult:
    result = run_kicad_cli(
        [
            "sch",
            "erc",
            "--output",
            str(report),
            str(schematic),
        ]
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "kicad-cli ERC failed"
        raise RuntimeError(message)
    return ErcResult(
        violations=_erc_violation_count(result.stdout),
        stdout=result.stdout,
        stderr=result.stderr,
        report=report,
    )


def _erc_violation_count(output: str) -> int:
    prefix = "Found "
    suffix = " violations"
    for line in output.splitlines():
        if line.startswith(prefix) and suffix in line:
            value = line.removeprefix(prefix).split(suffix, 1)[0]
            try:
                return int(value)
            except ValueError:
                break
    raise RuntimeError(f"missing ERC violation count in kicad-cli output: {output}")


def _format_signature(signature: frozenset[tuple[str, str]]) -> str:
    return ", ".join(f"{ref}.{pin}" for ref, pin in sorted(signature))


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

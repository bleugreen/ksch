import subprocess
from dataclasses import dataclass
from filecmp import dircmp
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file
from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedEndpoint, ResolvedProject

Pin = tuple[str, str]
EndpointKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class NetlistNet:
    name: str
    connections: set[Pin]


@dataclass(frozen=True)
class ErcResult:
    violations: int
    stdout: str
    stderr: str
    report: Path


@dataclass(frozen=True)
class NetMateMismatch:
    pin: Pin
    expected_net: str | None
    observed_net: str | None
    expected_mates: frozenset[Pin]
    observed_mates: frozenset[Pin]

    def format(self) -> str:
        return (
            f"{_format_pin(self.pin)} net-mates differ; "
            f"schema net {self.expected_net or '<none>'} mates "
            f"[{_format_pins(self.expected_mates)}]; "
            f"exported net {self.observed_net or '<missing>'} mates "
            f"[{_format_pins(self.observed_mates)}]"
        )


class _DisjointSet:
    def __init__(self) -> None:
        self._parents: dict[EndpointKey, EndpointKey] = {}

    def add(self, item: EndpointKey) -> None:
        self._parents.setdefault(item, item)

    def find(self, item: EndpointKey) -> EndpointKey:
        self.add(item)
        parent = self._parents[item]
        if parent != item:
            parent = self.find(parent)
            self._parents[item] = parent
        return parent

    def union(self, left: EndpointKey, right: EndpointKey) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parents[right_root] = left_root


def compare_netlist_to_schema(
    project: ResolvedProject,
    exported: dict[str, NetlistNet],
) -> list[str]:
    return [mismatch.format() for mismatch in net_mate_mismatches(project, exported)]


def net_mate_mismatches(
    project: ResolvedProject,
    exported: dict[str, NetlistNet],
) -> list[NetMateMismatch]:
    expected_mates, expected_nets = schema_net_mates(project)
    observed_mates, observed_nets = netlist_net_mates(exported, expected_mates.keys())
    findings: list[NetMateMismatch] = []
    for pin in sorted(expected_mates):
        expected = expected_mates[pin]
        observed = observed_mates.get(pin, frozenset())
        if expected != observed:
            findings.append(
                NetMateMismatch(
                    pin=pin,
                    expected_net=expected_nets.get(pin),
                    observed_net=observed_nets.get(pin),
                    expected_mates=expected,
                    observed_mates=observed,
                )
            )
    return findings


def schema_net_mates(project: ResolvedProject) -> tuple[dict[Pin, frozenset[Pin]], dict[Pin, str]]:
    groups, pin_nets = _schema_pin_groups(project)
    return _pin_mates(groups), pin_nets


def netlist_net_mates(
    nets: dict[str, NetlistNet],
    pins: set[Pin] | dict[Pin, object],
) -> tuple[dict[Pin, frozenset[Pin]], dict[Pin, str]]:
    pin_set = set(pins)
    groups: list[set[Pin]] = []
    pin_nets: dict[Pin, str] = {}
    for net in nets.values():
        connections = net.connections & pin_set
        if not connections:
            continue
        groups.append(connections)
        for pin in connections:
            pin_nets[pin] = net.name
    return _pin_mates(groups), pin_nets


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


def connectivity_signature(nets: dict[str, NetlistNet]) -> set[frozenset[Pin]]:
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


def _schema_pin_groups(project: ResolvedProject) -> tuple[list[set[Pin]], dict[Pin, str]]:
    dsu = _DisjointSet()
    pin_keys: dict[EndpointKey, Pin] = {}
    pin_nets: dict[Pin, str] = {}

    for sheet in project.sheets.values():
        for net_name, endpoints in sheet.nets.items():
            net_key = _net_key(sheet.path, net_name)
            dsu.add(net_key)
            for endpoint in endpoints:
                endpoint_key = _endpoint_key(endpoint)
                dsu.union(net_key, endpoint_key)
                if endpoint.kind is EndpointKind.SYMBOL_PIN:
                    pin = (endpoint.ref or "", endpoint.pin_number or "")
                    pin_keys[endpoint_key] = pin
                    pin_nets[pin] = net_name
                elif endpoint.kind is EndpointKind.SHEET_PORT:
                    child_port_key = _interface_port_key(
                        _child_sheet_path(sheet.path, endpoint.child_sheet or ""),
                        endpoint.port or "",
                    )
                    dsu.union(endpoint_key, child_port_key)
        for port in project.source.sheets[sheet.path].interface:
            dsu.union(_net_key(sheet.path, port), _interface_port_key(sheet.path, port))

    grouped: dict[EndpointKey, set[Pin]] = {}
    for endpoint_key, pin in pin_keys.items():
        grouped.setdefault(dsu.find(endpoint_key), set()).add(pin)
    return list(grouped.values()), pin_nets


def _pin_mates(groups: list[set[Pin]]) -> dict[Pin, frozenset[Pin]]:
    mates: dict[Pin, frozenset[Pin]] = {}
    for group in groups:
        frozen_group = frozenset(group)
        for pin in group:
            mates[pin] = frozen_group - {pin}
    return mates


def _endpoint_key(endpoint: ResolvedEndpoint) -> EndpointKey:
    if endpoint.kind is EndpointKind.SYMBOL_PIN:
        return (
            "pin",
            endpoint.sheet_path,
            endpoint.ref or "",
            endpoint.pin_number or "",
        )
    return (
        "sheet_port",
        endpoint.sheet_path,
        endpoint.child_sheet or "",
        endpoint.port or "",
    )


def _net_key(sheet_path: str, net_name: str) -> EndpointKey:
    return ("net", sheet_path, net_name, "")


def _interface_port_key(sheet_path: str, port: str) -> EndpointKey:
    return ("interface", sheet_path, port, "")


def _child_sheet_path(parent_path: str, child_sheet: str) -> str:
    if parent_path == "/":
        return f"/{child_sheet}"
    return f"{parent_path}/{child_sheet}"


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


def _format_pin(pin: Pin) -> str:
    return f"{pin[0]}.{pin[1]}"


def _format_pins(pins: frozenset[Pin]) -> str:
    return ", ".join(_format_pin(pin) for pin in sorted(pins))


def _format_signature(signature: frozenset[Pin]) -> str:
    return _format_pins(signature)


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

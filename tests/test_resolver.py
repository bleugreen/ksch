from pathlib import Path

import pytest

from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.resolver import LibraryContext, resolve_project


def _context() -> LibraryContext:
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    return LibraryContext(symbols=symbols.symbols, footprints={})


def test_resolves_pin_name_all_to_duplicate_numbers() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    resolved = resolve_project(project, _context())
    endpoints = resolved.sheets["/"].nets["+5V"]
    assert [endpoint.pin_number for endpoint in endpoints if endpoint.ref == "J1"] == ["A4", "B4"]
    assert [endpoint.text for endpoint in endpoints if endpoint.ref == "J1"] == [
        "J1.VBUS@A4",
        "J1.VBUS@B4",
    ]


def test_rejects_ambiguous_pin_without_all_or_number() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].nets["BAD"] = ["J1.D+"]
    with pytest.raises(KschError, match="J1.D\\+ is ambiguous"):
        resolve_project(project, _context())


def test_rejects_unknown_child_port() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].nets["BAD"] = ["usb.NO_SUCH_PORT"]
    with pytest.raises(KschError, match="unknown sheet port usb.NO_SUCH_PORT"):
        resolve_project(project, _context())


def test_rejects_same_pin_on_multiple_nets() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].nets["ALIAS_A"] = ["J1.VBUS@A4"]
    project.sheets["/"].nets["ALIAS_B"] = ["J1.VBUS@A4"]

    with pytest.raises(KschError, match="J1.VBUS@A4 is connected to both"):
        resolve_project(project, _context())


def test_rejects_no_connect_on_connected_pin() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].no_connects.append("J1.D+@A6")

    with pytest.raises(KschError, match="J1.D\\+@A6 is connected to USB_UP_DP"):
        resolve_project(project, _context())


def test_rejects_no_connect_that_expands_to_connected_duplicate_pin() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].no_connects.append("J1.VBUS/all")

    with pytest.raises(KschError, match="J1.VBUS@A4 is connected to \\+5V"):
        resolve_project(project, _context())

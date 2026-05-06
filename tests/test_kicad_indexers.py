from pathlib import Path

from ksch.kicad.footprints import index_footprint_library
from ksch.kicad.symbols import index_symbol_library


def test_symbol_index_extracts_duplicate_pin_names() -> None:
    index = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    symbol = index.symbols["Test:USB_C"]
    d_plus = [pin.number for pin in symbol.pins if pin.name == "D+"]
    assert d_plus == ["A6", "B6"]


def test_symbol_index_extracts_default_footprint() -> None:
    index = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    assert index.symbols["Test:USBHub"].footprint == "TestFootprints:USB_Test"


def test_symbol_index_resolves_extended_symbol_pins_and_overrides() -> None:
    index = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    symbol = index.symbols["Test:DerivedSwitch"]
    assert [(pin.name, pin.number) for pin in symbol.pins] == [("GND", "1"), ("OUT", "2")]
    assert symbol.footprint == "TestFootprints:Derived"
    assert symbol.definition is not None
    assert "extends" not in str(symbol.definition)
    assert "DerivedSwitch_1_1" in str(symbol.definition)


def test_symbol_index_keeps_real_symbols_with_numeric_suffixes() -> None:
    index = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))

    assert "Test:Connector_8" in index.symbols
    assert "Test:Connector_8_1_1" not in index.symbols


def test_footprint_index_extracts_pads() -> None:
    index = index_footprint_library(
        "TestFootprints",
        Path("tests/fixtures/kicad/footprints/TestFootprints.pretty"),
    )
    footprint = index.footprints["TestFootprints:USB_Test"]
    assert sorted(footprint.pads) == ["A4", "A6", "A7", "B4", "B6", "B7"]

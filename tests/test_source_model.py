from pathlib import Path

from ksch.model.source import SourceDocument
from ksch.schema.loader import load_yaml_text


def test_project_document_accepts_hierarchy() -> None:
    data = load_yaml_text(
        """
ksch: 1
project:
  name: demo
sheets:
  usb:
    source: sheets/usb.ksch.yaml
    connects:
      VBUS: +5V
symbols:
  J1:
    lib: Test:USB_C
    connects:
      VBUS/all: +5V
""",
        Path("project.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.ksch == 1
    assert document.project is not None
    assert document.sheets["usb"].source == Path("sheets/usb.ksch.yaml")
    assert document.sheets["usb"].connects["VBUS"] == "+5V"
    assert document.symbols["J1"].connects["VBUS/all"] == "+5V"


def test_sheet_document_accepts_interface() -> None:
    data = load_yaml_text(
        """
ksch: 1
sheet:
  id: usb
interface:
  VBUS: power_in
symbols:
  U2:
    lib: Test:USBHub
    connects:
      VBUS_DET: VBUS
""",
        Path("usb.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.sheet is not None
    assert document.interface["VBUS"] == "power_in"


def test_sheet_document_accepts_power_flags() -> None:
    data = load_yaml_text(
        """
ksch: 1
sheet:
  id: power
symbols:
  U1:
    lib: Test:Regulator
    connects:
      VOUT: +5V
power_flags:
  - +5V
""",
        Path("power.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.power_flags == ["+5V"]


def test_symbol_connects_accepts_no_connect_sentinel() -> None:
    data = load_yaml_text(
        """
ksch: 1
project:
  name: demo
symbols:
  J1:
    lib: Test:USB_C
    connects:
      D-/all: nc
""",
        Path("project.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.symbols["J1"].connects["D-/all"] == "nc"

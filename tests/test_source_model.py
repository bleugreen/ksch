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
symbols:
  J1:
    lib: Test:USB_C
nets:
  +5V:
    - J1.VBUS/all
    - usb.VBUS
""",
        Path("project.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.ksch == 1
    assert document.project is not None
    assert document.sheets["usb"].source == Path("sheets/usb.ksch.yaml")


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
nets:
  VBUS:
    - U2.VBUS_DET
""",
        Path("usb.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.sheet is not None
    assert document.interface["VBUS"] == "power_in"

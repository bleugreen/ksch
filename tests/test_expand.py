from pathlib import Path

from ksch.expand import load_project_ir

FIXTURE = Path("tests/fixtures/project/project.ksch.yaml")


def test_load_project_ir_with_child_sheet() -> None:
    project = load_project_ir(FIXTURE)
    assert project.name == "demo"
    assert "/" in project.sheets
    assert "/usb" in project.sheets
    assert project.sheets["/usb"].interface["VBUS"] == "power_in"
    assert project.sheets["/"].child_instances["usb"].target_path == "/usb"


def test_root_net_can_target_child_port() -> None:
    project = load_project_ir(FIXTURE)
    root = project.sheets["/"]
    assert root.nets["+5V"] == ["J1.VBUS/all", "usb.VBUS"]

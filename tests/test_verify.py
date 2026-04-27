from pathlib import Path

from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedEndpoint, ResolvedProject, ResolvedSheet
from ksch.verify import NetlistNet, compare_connectivity, compare_dirs


def test_compare_connectivity_reports_missing_pin() -> None:
    project = ResolvedProject(name="demo", source=None)  # type: ignore[arg-type]
    project.sheets["/"] = ResolvedSheet(
        path="/",
        nets={
            "USB_DP": [
                ResolvedEndpoint(
                    text="J1.D+@A6",
                    kind=EndpointKind.SYMBOL_PIN,
                    sheet_path="/",
                    ref="J1",
                    pin_name="D+",
                    pin_number="A6",
                )
            ]
        },
    )
    exported = {"USB_DP": NetlistNet(name="USB_DP", connections=set())}
    findings = compare_connectivity(project, exported)
    assert findings == ["USB_DP missing J1.A6"]


def test_compare_connectivity_accepts_matching_pin() -> None:
    project = ResolvedProject(name="demo", source=None)  # type: ignore[arg-type]
    project.sheets["/"] = ResolvedSheet(
        path="/",
        nets={
            "USB_DP": [
                ResolvedEndpoint(
                    text="J1.D+@A6",
                    kind=EndpointKind.SYMBOL_PIN,
                    sheet_path="/",
                    ref="J1",
                    pin_name="D+",
                    pin_number="A6",
                )
            ]
        },
    )
    exported = {"USB_DP": NetlistNet(name="USB_DP", connections={("J1", "A6")})}
    assert compare_connectivity(project, exported) == []


def test_compare_dirs_reports_missing_unexpected_and_different_files(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    (expected / "sheets").mkdir(parents=True)
    (actual / "sheets").mkdir(parents=True)
    (expected / "same.txt").write_text("same", encoding="utf-8")
    (actual / "same.txt").write_text("same", encoding="utf-8")
    (expected / "missing.txt").write_text("missing", encoding="utf-8")
    (actual / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    (expected / "sheets" / "changed.txt").write_text("before", encoding="utf-8")
    (actual / "sheets" / "changed.txt").write_text("after", encoding="utf-8")

    assert compare_dirs(expected, actual) == [
        "missing generated file missing.txt",
        "unexpected generated file unexpected.txt",
        "generated file differs sheets/changed.txt",
    ]

from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedEndpoint, ResolvedProject, ResolvedSheet
from ksch.verify import NetlistNet, compare_connectivity


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

from ksch.model.endpoint import Endpoint, EndpointKind, parse_endpoint


def test_parse_named_pin_endpoint() -> None:
    endpoint = parse_endpoint("U2.USBDP_DN4/PRT_DIS_P4")
    assert endpoint == Endpoint(
        kind=EndpointKind.SYMBOL_PIN,
        ref="U2",
        pin_name="USBDP_DN4/PRT_DIS_P4",
        pin_number=None,
        all_matching=False,
        sheet=None,
        port=None,
    )


def test_parse_named_pin_with_number() -> None:
    endpoint = parse_endpoint("U1.GND@42")
    assert endpoint.ref == "U1"
    assert endpoint.pin_name == "GND"
    assert endpoint.pin_number == "42"


def test_parse_all_matching_pin_name() -> None:
    endpoint = parse_endpoint("J2.VBUS/all")
    assert endpoint.ref == "J2"
    assert endpoint.pin_name == "VBUS"
    assert endpoint.all_matching is True


def test_parse_child_sheet_port() -> None:
    endpoint = parse_endpoint("usb.VBUS")
    assert endpoint.kind is EndpointKind.SHEET_PORT
    assert endpoint.sheet == "usb"
    assert endpoint.port == "VBUS"

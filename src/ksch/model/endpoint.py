from enum import StrEnum

from pydantic import BaseModel


class EndpointKind(StrEnum):
    SYMBOL_PIN = "symbol_pin"
    SHEET_PORT = "sheet_port"


class Endpoint(BaseModel, frozen=True):
    kind: EndpointKind
    ref: str | None = None
    pin_name: str | None = None
    pin_number: str | None = None
    all_matching: bool = False
    sheet: str | None = None
    port: str | None = None


def parse_endpoint(text: str) -> Endpoint:
    head, sep, tail = text.partition(".")
    if not sep or not head or not tail:
        raise ValueError(f"invalid endpoint '{text}'")

    all_matching = False
    if tail.endswith("/all"):
        tail = tail[:-4]
        all_matching = True

    pin_name = tail
    pin_number = None
    if "@" in tail:
        pin_name, pin_number = tail.rsplit("@", 1)
        if not pin_name or not pin_number:
            raise ValueError(f"invalid endpoint '{text}'")

    if head[:1].isupper():
        return Endpoint(
            kind=EndpointKind.SYMBOL_PIN,
            ref=head,
            pin_name=pin_name,
            pin_number=pin_number,
            all_matching=all_matching,
        )

    if "@" in tail or all_matching:
        raise ValueError(f"sheet port endpoint cannot use pin disambiguation: '{text}'")
    return Endpoint(kind=EndpointKind.SHEET_PORT, sheet=head, port=tail)

import asyncio

from ksch.mcp.server import create_server, symbol_info_text


def test_symbol_info_text_returns_pin_details() -> None:
    text = symbol_info_text(
        "Test:USB_C",
        ["Test=tests/fixtures/kicad/symbols/Test.kicad_sym"],
    )

    assert "Test:USB_C" in text
    assert "D+@A6" in text


def test_mcp_module_imports() -> None:
    server = create_server()
    assert server.name == "kicad-schema"


def test_mcp_server_exposes_symbol_info_tool() -> None:
    server = create_server()

    tools = asyncio.run(server.list_tools())

    assert [tool.name for tool in tools] == ["symbol_info"]

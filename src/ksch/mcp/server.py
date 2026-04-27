from mcp.server.fastmcp import FastMCP

from ksch.authoring import load_symbol_libraries, symbol_info_lines


def symbol_info_text(lib_id: str, libraries: list[str]) -> str:
    symbol = load_symbol_libraries(libraries)[lib_id]
    return "\n".join(symbol_info_lines(symbol))


def create_server() -> FastMCP:
    server = FastMCP("kicad-schema")

    @server.tool()
    def symbol_info(lib_id: str, libraries: list[str]) -> str:
        return symbol_info_text(lib_id, libraries)

    return server

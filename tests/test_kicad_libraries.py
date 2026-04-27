from pathlib import Path

from ksch.kicad.libraries import parse_library_table


def test_parse_symbol_library_table_with_project_variable() -> None:
    table = parse_library_table(
        Path("tests/fixtures/kicad/sym-lib-table"),
        variables={"KIPRJMOD": str(Path("tests/fixtures/kicad").resolve())},
    )
    assert table.kind == "sym_lib_table"
    assert table.entries["Test"].path.name == "Test.kicad_sym"


def test_parse_footprint_library_table_with_project_variable() -> None:
    table = parse_library_table(
        Path("tests/fixtures/kicad/fp-lib-table"),
        variables={"KIPRJMOD": str(Path("tests/fixtures/kicad").resolve())},
    )
    assert table.kind == "fp_lib_table"
    assert table.entries["TestFootprints"].path.name == "TestFootprints.pretty"

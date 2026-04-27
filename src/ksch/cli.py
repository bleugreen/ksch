from typing import Annotated

import typer

from ksch import __version__

app = typer.Typer(
    add_completion=False,
    help="Canonical text-first schematic compiler for KiCad.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ksch {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            help="Show the version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Canonical text-first schematic compiler for KiCad."""

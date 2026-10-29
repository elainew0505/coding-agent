from importlib.metadata import version
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"coding-agent {version('coding-agent')}")
        raise typer.Exit()


@app.callback()
def main(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Run Claude as a coding agent inside a Docker sandbox."""

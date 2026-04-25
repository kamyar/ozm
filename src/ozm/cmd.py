#!/usr/bin/env python3
"""Arbitrary command pass-through with approval dialog."""

import subprocess
import sys

import click

from ozm.approve import request_cmd_approval


@click.command(
    "cmd",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("command_and_args", nargs=-1, type=click.UNPROCESSED, required=True)
def cmd_cmd(command_and_args: tuple[str, ...]) -> None:
    """Run an arbitrary command after approval."""
    if not command_and_args:
        raise click.ClickException("Nothing to run.")

    command = " ".join(command_and_args)
    approved = request_cmd_approval(command)

    if approved is True:
        click.echo(f"ozm: approved cmd")
        result = subprocess.run(command, shell=True)
        sys.exit(result.returncode)

    if approved is False:
        click.echo("ozm: denied cmd")
        sys.exit(1)

    click.echo(f"ozm: {command}")
    click.echo("No approval dialog available. Review the command above.")
    sys.exit(1)

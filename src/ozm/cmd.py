#!/usr/bin/env python3
"""Arbitrary command pass-through."""

import subprocess
import sys

import click


@click.command(
    "cmd",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("command_and_args", nargs=-1, type=click.UNPROCESSED, required=True)
def cmd_cmd(command_and_args: tuple[str, ...]) -> None:
    """Run an arbitrary command (pass-through)."""
    if not command_and_args:
        raise click.ClickException("Nothing to run.")

    result = subprocess.run(" ".join(command_and_args), shell=True)
    sys.exit(result.returncode)

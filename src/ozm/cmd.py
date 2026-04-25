#!/usr/bin/env python3
"""Arbitrary command pass-through with approval dialog."""

import hashlib
import subprocess
import sys

import click

from ozm.approve import request_cmd_approval
from ozm.config import is_command_allowed, project_key
from ozm.run import load_hashes, save_hashes

CMD_PREFIX = "cmd:"


def _cmd_hash(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


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

    if is_command_allowed(command):
        result = subprocess.run(command, shell=True)
        sys.exit(result.returncode)

    key = project_key(CMD_PREFIX + command)
    current_hash = _cmd_hash(command)
    hashes = load_hashes()

    if hashes.get(key) == current_hash:
        result = subprocess.run(command, shell=True)
        sys.exit(result.returncode)

    approval = request_cmd_approval(command)

    if approval.approved is True:
        hashes[key] = current_hash
        save_hashes(hashes)
        click.echo("ozm: approved cmd")
        result = subprocess.run(command, shell=True)
        sys.exit(result.returncode)

    if approval.approved is False:
        if approval.feedback:
            click.echo(f"ozm: denied cmd — {approval.feedback}", err=True)
        else:
            click.echo("ozm: denied cmd", err=True)
        sys.exit(1)

    click.echo(f"ozm: {command}")
    click.echo("No approval dialog available. Review the command above.")
    sys.exit(1)

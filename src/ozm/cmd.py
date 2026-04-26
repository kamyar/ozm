#!/usr/bin/env python3
"""Arbitrary command pass-through with approval dialog."""

import hashlib
import os
import subprocess
import sys

import click

from ozm.approve import request_cmd_approval
from ozm.audit import log as audit_log
from ozm.config import add_allowed_command, is_command_allowed, is_command_blocked, project_key
from ozm.run import load_hashes, save_hashes

CMD_PREFIX = "cmd:"


def _cmd_hash(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


WRAPPERS = {"uv", "npx", "bunx", "poetry", "pipx", "run", "exec"}
INTERPRETERS = {"python", "python3", "bash", "sh", "zsh", "node", "ruby", "perl"}


def _find_script_in_args(args: tuple[str, ...]) -> tuple[str, str] | None:
    """Return (script_path, suggested_shebang) if args look like script execution."""
    interpreter = None
    for arg in args:
        if arg.startswith("-"):
            continue
        if arg in WRAPPERS:
            continue
        _, ext = os.path.splitext(arg)
        if ext and os.path.isfile(arg) and interpreter:
            shebang = f"#!/usr/bin/env {interpreter}"
            return arg, shebang
        if not interpreter and "/" not in arg and arg in INTERPRETERS:
            interpreter = arg
    return None


@click.command(
    "cmd",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("command_and_args", nargs=-1, type=click.UNPROCESSED, required=True)
def cmd_cmd(command_and_args: tuple[str, ...]) -> None:
    """Run an arbitrary command after approval."""
    if not command_and_args:
        raise click.ClickException("Nothing to run.")

    match = _find_script_in_args(command_and_args)
    if match:
        script, shebang = match
        click.echo(
            f"ozm: use 'ozm run {script}' instead — "
            f"make sure the script has a shebang ({shebang})",
            err=True,
        )
        sys.exit(1)

    command = " ".join(command_and_args)

    blocked = is_command_blocked(command)
    if blocked:
        audit_log("blocked", "cmd", command)
        click.echo(f"ozm: blocked by pattern '{blocked}' in .ozm.yaml", err=True)
        sys.exit(1)

    if is_command_allowed(command):
        audit_log("config", "cmd", command)
        result = subprocess.run(command, shell=True)
        sys.exit(result.returncode)

    key = project_key(CMD_PREFIX + command)
    current_hash = _cmd_hash(command)
    hashes = load_hashes()

    if hashes.get(key) == current_hash:
        audit_log("cached", "cmd", command)
        result = subprocess.run(command, shell=True)
        sys.exit(result.returncode)

    approval = request_cmd_approval(command)

    if approval.approved is True:
        run_command = approval.command or command
        if approval.allow_pattern:
            add_allowed_command(approval.allow_pattern)
            click.echo(f"ozm: added allowlist pattern: {approval.allow_pattern}", err=True)
        run_key = project_key(CMD_PREFIX + run_command)
        run_hash = _cmd_hash(run_command)
        hashes[run_key] = run_hash
        save_hashes(hashes)
        audit_log("clicked", "cmd", run_command, approval.feedback)
        if run_command != command:
            click.echo(f"ozm: approved cmd (edited)", err=True)
        result = subprocess.run(run_command, shell=True)
        sys.exit(result.returncode)

    if approval.approved is False:
        audit_log("denied", "cmd", command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: denied cmd — {approval.feedback}", err=True)
        else:
            click.echo("ozm: denied cmd", err=True)
        sys.exit(1)

    audit_log("no-dialog", "cmd", command, approval.feedback)
    click.echo(f"ozm: {command}")
    if approval.feedback:
        click.echo(f"ozm: dialog error: {approval.feedback}", err=True)
    click.echo(
        "ozm: BLOCKED — approval dialog could not be displayed. "
        "The command was NOT executed. "
        "Do NOT retry. "
        "Tell the user ozm needs a macOS GUI session to approve this command.",
        err=True,
    )
    sys.exit(1)

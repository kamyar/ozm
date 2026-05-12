#!/usr/bin/env python3
"""Arbitrary command pass-through with approval dialog."""

import hashlib
import os
import shlex
import subprocess
import sys

import click

from ozm.agent import extract_agent_metadata
from ozm.approve import request_cmd_approval, request_override
from ozm.audit import log as audit_log
from ozm.config import (
    add_allowed_command,
    add_blocked_command,
    command_name,
    disallowed_command_reason,
    has_shell_metacharacters,
    is_command_allowed,
    is_command_blocked,
    project_key,
)
from ozm.run import load_hashes, save_hashes

CMD_PREFIX = "cmd:"


def _cmd_hash(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _scope_label(global_scope: bool) -> str:
    return "global" if global_scope else "project"


def _run_command(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv)


def _edited_argv(command: str) -> list[str]:
    if has_shell_metacharacters(command):
        raise click.ClickException(
            "edited command contains shell syntax; use argv-style arguments or "
            "put shell logic in a reviewed script"
        )
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise click.ClickException(f"edited command could not be parsed: {exc}") from exc
    if not argv:
        raise click.ClickException("edited command is empty")
    return argv


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

    args = list(command_and_args)
    args, agent = extract_agent_metadata(args)
    if not args:
        raise click.ClickException("Nothing to run.")
    reason = None
    if "--reason" in args:
        idx = args.index("--reason")
        if idx + 1 < len(args):
            reason = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
    for i, a in enumerate(list(args)):
        if a.startswith("--reason="):
            reason = a.split("=", 1)[1]
            args.pop(i)
            break

    match = _find_script_in_args(tuple(args))
    if match:
        script, shebang = match
        click.echo(
            "ozm: use "
            f"'ozm run --agent-name \"{agent.name}\" "
            f"--agent-description \"{agent.description}\" {script}' instead — "
            f"make sure the script has a shebang ({shebang})",
            err=True,
        )
        sys.exit(1)

    if args and args[0] == "git":
        click.echo(
            "ozm: use "
            "'ozm git --agent-name \"<work>\" "
            "--agent-description \"<intent>\" <subcommand>' "
            "instead of 'ozm cmd git ...'",
            err=True,
        )
        sys.exit(1)

    command = shlex.join(args)
    disallowed = disallowed_command_reason(command)
    if disallowed:
        audit_log("blocked", "cmd", command)
        click.echo(f"ozm: blocked command '{command_name(command)}'", err=True)
        click.echo(f"ozm: {disallowed}", err=True)
        sys.exit(1)

    blocked = is_command_blocked(command)
    if blocked:
        if not reason:
            audit_log("blocked", "cmd", command)
            click.echo(f"ozm: blocked by pattern '{blocked}' in config", err=True)
            click.echo("ozm: use --reason \"justification\" to request a one-time override", err=True)
            sys.exit(1)
        approval = request_override(command, f"blocked by pattern '{blocked}'", reason, agent)
        if approval.approved is True:
            audit_log("override", "cmd", command, approval.feedback)
            click.echo("ozm: override granted (one-time)", err=True)
            result = _run_command(args)
            sys.exit(result.returncode)
        else:
            audit_log("denied", "cmd", command, approval.feedback)
            click.echo("ozm: override denied", err=True)
            sys.exit(1)

    if is_command_allowed(command):
        audit_log("config", "cmd", command)
        click.echo("ozm: allowed (config)", err=True)
        result = _run_command(args)
        sys.exit(result.returncode)

    key = project_key(CMD_PREFIX + command)
    current_hash = _cmd_hash(command)
    hashes = load_hashes()

    if hashes.get(key) == current_hash:
        audit_log("cached", "cmd", command)
        click.echo("ozm: allowed (cached)", err=True)
        result = _run_command(args)
        sys.exit(result.returncode)

    approval = request_cmd_approval(command, agent)

    if approval.approved is True:
        run_command = approval.command or command
        run_args = args
        if run_command != command:
            run_args = _edited_argv(run_command)
            run_command = shlex.join(run_args)
        run_disallowed = disallowed_command_reason(run_command)
        if run_disallowed:
            audit_log("blocked", "cmd", run_command)
            click.echo(f"ozm: blocked command '{command_name(run_command)}'", err=True)
            click.echo(f"ozm: {run_disallowed}", err=True)
            sys.exit(1)
        if run_command != command:
            recheck = is_command_blocked(run_command)
            if recheck:
                audit_log("blocked", "cmd", run_command)
                click.echo(f"ozm: edited command blocked by pattern '{recheck}'", err=True)
                sys.exit(1)
        allow_pattern = approval.allow_pattern
        if approval.apply_globally and not allow_pattern:
            allow_pattern = run_command
        if allow_pattern:
            scope = _scope_label(approval.apply_globally)
            if add_allowed_command(allow_pattern, global_scope=approval.apply_globally):
                click.echo(
                    f"ozm: added {scope} allowlist pattern: {allow_pattern}",
                    err=True,
                )
            else:
                reason = disallowed_command_reason(allow_pattern)
                click.echo(
                    f"ozm: not adding allowlist pattern '{allow_pattern}': {reason}",
                    err=True,
                )
        run_key = project_key(CMD_PREFIX + run_command)
        run_hash = _cmd_hash(run_command)
        hashes[run_key] = run_hash
        save_hashes(hashes)
        audit_log("clicked", "cmd", run_command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: approved cmd — {approval.feedback}", err=True)
        elif run_command != command:
            click.echo(f"ozm: approved cmd (edited)", err=True)
        else:
            click.echo(f"ozm: approved cmd", err=True)
        result = _run_command(run_args)
        sys.exit(result.returncode)

    if approval.approved is False:
        block_pattern = approval.block_pattern
        if approval.apply_globally and not block_pattern:
            block_pattern = approval.command or command
        if block_pattern:
            scope = _scope_label(approval.apply_globally)
            if add_blocked_command(block_pattern, global_scope=approval.apply_globally):
                click.echo(
                    f"ozm: added {scope} blocklist pattern: {block_pattern}",
                    err=True,
                )
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

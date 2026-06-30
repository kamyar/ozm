#!/usr/bin/env python3
"""Arbitrary command pass-through with approval dialog."""

import hashlib
import os
import re
import shlex
import subprocess
import sys

import click

from ozm.agent import extract_agent_metadata
from ozm.approve import request_cmd_approval, request_override
from ozm.audit import log as audit_log
from ozm.exit_codes import BLOCKED, CONFIG_ERROR, DENIED, NO_DIALOG, click_error
from ozm.config import (
    add_allowed_command,
    add_blocked_command,
    command_name,
    command_parts,
    disallowed_command_reason,
    has_shell_metacharacters,
    is_command_allowed,
    is_command_blocked,
    project_key,
)
from ozm.github_graphql import read_only_reason as github_graphql_read_only_reason
from ozm.run import load_hashes, save_hashes

CMD_PREFIX = "cmd:"
SAFE_READ_ONLY_COMMANDS = {"echo", "printf", "pwd", "date", "true", "false", "test"}


def _cmd_hash(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _scope_label(global_scope: bool) -> str:
    return "global" if global_scope else "project"


def _run_command(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv)


def _is_env_assignment_token(token: str) -> bool:
    name, sep, _value = token.partition("=")
    return bool(sep and name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def _safe_read_only_reason(command: str) -> str | None:
    if os.environ.get("OZM_SAFE_READONLY") != "1":
        return None
    parts = command_parts(command)
    if not parts:
        return None
    first = os.path.basename(parts[0])
    if first != "env" and _is_env_assignment_token(parts[0]):
        return None
    name = command_name(command)
    if name in SAFE_READ_ONLY_COMMANDS:
        return f"read-only {name}"
    return None


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

EXTENSION_SHEBANGS = {
    ".py": "#!/usr/bin/env python3",
    ".sh": "#!/usr/bin/env bash",
    ".rb": "#!/usr/bin/env ruby",
    ".pl": "#!/usr/bin/env perl",
    ".js": "#!/usr/bin/env node",
}


def _detect_inline_code(args: tuple[str, ...]) -> str | None:
    """Return interpreter name if args look like inline code (e.g. python -c 'code')."""
    interpreter = None
    for arg in args:
        if arg in WRAPPERS:
            continue
        if not interpreter and arg in INTERPRETERS:
            interpreter = arg
            continue
        if interpreter and arg == "-c":
            return interpreter
        if not arg.startswith("-"):
            return None
    return None


def _find_script_in_args(args: tuple[str, ...]) -> tuple[str, str] | None:
    """Return (script_path, suggested_shebang) if args look like script execution."""
    interpreter = None
    saw_wrapper = False
    for arg in args:
        if arg.startswith("-"):
            if interpreter and arg == "-m":
                return None
            continue
        if arg in WRAPPERS:
            saw_wrapper = True
            continue
        _, ext = os.path.splitext(arg)
        if ext and os.path.isfile(arg):
            if interpreter:
                shebang = f"#!/usr/bin/env {interpreter}"
                return arg, shebang
            inferred = EXTENSION_SHEBANGS.get(ext)
            if saw_wrapper and inferred:
                return arg, inferred
        if not interpreter and "/" not in arg and arg in INTERPRETERS:
            interpreter = arg
            continue
        # Non-wrapper, non-interpreter, non-script token (e.g. "pytest") —
        # everything after this is arguments to that command, not scripts.
        if not interpreter:
            return None
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

    inline = _detect_inline_code(tuple(args))
    if inline:
        shebang = f"#!/usr/bin/env {inline}"
        click.echo(
            f"ozm: write the code to a script file with a shebang ({shebang}) "
            f"and use 'ozm run --agent-name \"{agent.name}\" "
            f"--agent-description \"{agent.description}\" <script>' "
            f"instead of '{inline} -c'",
            err=True,
        )
        sys.exit(1)

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
        sys.exit(BLOCKED)

    if args and args[0] == "git":
        click.echo(
            "ozm: use "
            "'ozm git --agent-name \"<work>\" "
            "--agent-description \"<intent>\" <subcommand>' "
            "instead of 'ozm cmd git ...'",
            err=True,
        )
        sys.exit(BLOCKED)

    command = shlex.join(args)
    disallowed = disallowed_command_reason(command)
    if disallowed:
        audit_log("blocked", "cmd", command)
        click.echo(f"ozm: blocked command '{command_name(command)}'", err=True)
        click.echo(f"ozm: {disallowed}", err=True)
        sys.exit(BLOCKED)

    try:
        blocked = is_command_blocked(command)
    except (OSError, RuntimeError) as exc:
        audit_log("error", "cmd", command, str(exc))
        raise click_error(
            f"config error: {exc}. The command was NOT executed.",
            CONFIG_ERROR,
        ) from exc
    if blocked:
        if not reason:
            audit_log("blocked", "cmd", command)
            click.echo(f"ozm: blocked by pattern '{blocked}' in config", err=True)
            click.echo("ozm: use --reason \"justification\" to request a one-time override", err=True)
            sys.exit(BLOCKED)
        approval = request_override(command, f"blocked by pattern '{blocked}'", reason, agent)
        if approval.approved is True:
            audit_log("override", "cmd", command, approval.feedback)
            click.echo("ozm: override granted (one-time)", err=True)
            result = _run_command(args)
            sys.exit(result.returncode)
        else:
            audit_log("denied", "cmd", command, approval.feedback)
            click.echo("ozm: override denied", err=True)
            sys.exit(DENIED)

    semantic_reason = _safe_read_only_reason(command)
    if not semantic_reason:
        semantic_reason = github_graphql_read_only_reason(args)
    if semantic_reason:
        audit_log("semantic", "cmd", command, semantic_reason)
        click.echo(f"ozm: allowed ({semantic_reason})", err=True)
        result = _run_command(args)
        sys.exit(result.returncode)

    try:
        allowed = is_command_allowed(command)
    except (OSError, RuntimeError) as exc:
        audit_log("error", "cmd", command, str(exc))
        raise click_error(
            f"config error: {exc}. The command was NOT executed.",
            CONFIG_ERROR,
        ) from exc
    if allowed:
        audit_log("config", "cmd", command)
        click.echo("ozm: allowed (config)", err=True)
        result = _run_command(args)
        sys.exit(result.returncode)

    key = project_key(CMD_PREFIX + command)
    current_hash = _cmd_hash(command)
    try:
        hashes = load_hashes()
    except (OSError, RuntimeError) as exc:
        audit_log("error", "cmd", command, str(exc))
        raise click_error(
            f"approval cache error: {exc}. The command was NOT executed.",
            CONFIG_ERROR,
        ) from exc

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
            sys.exit(BLOCKED)
        if run_command != command:
            recheck = is_command_blocked(run_command)
            if recheck:
                audit_log("blocked", "cmd", run_command)
                click.echo(f"ozm: edited command blocked by pattern '{recheck}'", err=True)
                sys.exit(BLOCKED)
        allow_pattern = approval.allow_pattern
        if approval.apply_globally and not allow_pattern:
            allow_pattern = run_command
        if allow_pattern:
            scope = _scope_label(approval.apply_globally)
            try:
                added = add_allowed_command(allow_pattern, global_scope=approval.apply_globally)
            except (OSError, RuntimeError) as exc:
                audit_log("error", "cmd", run_command, str(exc))
                raise click_error(
                    f"could not save {scope} allowlist pattern '{allow_pattern}': {exc}. "
                    "The command was NOT executed.",
                    CONFIG_ERROR,
                ) from exc
            if added:
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
        try:
            save_hashes(hashes)
        except (OSError, RuntimeError) as exc:
            audit_log("error", "cmd", run_command, str(exc))
            raise click_error(
                f"could not save approval cache: {exc}. The command was NOT executed.",
                CONFIG_ERROR,
            ) from exc
        audit_log("clicked", "cmd", run_command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: approved cmd — [user] {approval.feedback}", err=True)
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
            try:
                added = add_blocked_command(block_pattern, global_scope=approval.apply_globally)
            except (OSError, RuntimeError) as exc:
                audit_log("error", "cmd", command, str(exc))
                raise click_error(
                    f"could not save {scope} blocklist pattern '{block_pattern}': {exc}. "
                    "The command was NOT executed.",
                    CONFIG_ERROR,
                ) from exc
            if added:
                click.echo(
                    f"ozm: added {scope} blocklist pattern: {block_pattern}",
                    err=True,
                )
        audit_log("denied", "cmd", command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: denied cmd — [user] {approval.feedback}", err=True)
        else:
            click.echo("ozm: denied cmd", err=True)
        sys.exit(DENIED)

    audit_log("no-dialog", "cmd", command, approval.feedback)
    click.echo(f"ozm: {command}")
    if approval.feedback:
        click.echo(f"ozm: dialog error: [ozm] {approval.feedback}", err=True)
    click.echo(
        "ozm: BLOCKED — approval dialog could not be displayed. "
        "The command was NOT executed. "
        "Do NOT retry. "
        "Tell the user ozm needs a macOS GUI session to approve this command.",
        err=True,
    )
    sys.exit(NO_DIALOG)

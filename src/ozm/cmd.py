#!/usr/bin/env python3
"""Arbitrary command pass-through with approval dialog."""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import click

from ozm.agent import extract_agent_metadata
from ozm.approve import request_cmd_approval, request_override
from ozm.audit import log as audit_log
from ozm.exit_codes import BLOCKED, CONFIG_ERROR, DENIED, NO_DIALOG, click_error
from ozm.config import (
    _command_start_index,
    add_allowed_command,
    add_blocked_command,
    command_name,
    command_parts,
    disallowed_command_reason,
    github_operation_allowed,
    has_shell_metacharacters,
    is_command_allowed,
    is_command_blocked,
    project_key,
)
from ozm.github_api import read_only_reason as github_api_read_only_reason
from ozm.github_operations import (
    AddSubIssueOperation,
    ReviewReplyOperation,
    github_operation_execution_args,
    match_supported_raw_write,
    parse_typed_operation,
)
from ozm.output_filter import output_filter_active, run_with_output_filter
from ozm.paths import trusted_executable
from ozm.run import load_hashes, save_hashes

CMD_PREFIX = "cmd:"
RECENT_CHMOD_WINDOW_SECONDS = 10 * 60
CONFIRM_RECENT_CHMOD_FLAG = "--confirm-recent-chmod"
SAFE_READ_ONLY_COMMANDS = {"echo", "printf", "pwd", "date", "true", "false", "test"}
_CHMOD_SHORT_OPTIONS = frozenset("cfhRvHLP")


def _cmd_hash(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _scope_label(global_scope: bool) -> str:
    return "global" if global_scope else "project"


def _run_command(argv: list[str]) -> subprocess.CompletedProcess:
    execution_argv = list(argv)
    typed_github_argv = github_operation_execution_args(execution_argv)
    if typed_github_argv is not None:
        execution_argv = typed_github_argv
    if execution_argv and execution_argv[0] == "gh":
        gh = trusted_executable("gh")
        if gh is None:
            raise click.ClickException(
                "trusted gh executable was not found in a system location"
            )
        execution_argv[0] = gh
    if output_filter_active():
        return run_with_output_filter(execution_argv)
    return subprocess.run(execution_argv)


def _is_env_assignment_token(token: str) -> bool:
    name, sep, _value = token.partition("=")
    return bool(sep and name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def _builtin_read_only_reason(args: list[str]) -> str | None:
    """Return a reason for conservative, operation-aware local reads."""
    if not args:
        return None
    command = os.path.basename(args[0])
    rest = args[1:]
    if (
        command == "command"
        and len(rest) >= 2
        and rest[0] == "-v"
        and all(not item.startswith("-") for item in rest[1:])
    ):
        return "command lookup"
    if command == "bazel" and rest[:1] == ["query"]:
        if any(
            item == "--output_file" or item.startswith("--output_file=")
            for item in rest[1:]
        ):
            return None
        return "bazel query"
    if command == "brew" and rest[:1] == ["search"]:
        return "brew search"
    if command == "npm" and rest and rest[0] in ("view", "list"):
        return f"npm {rest[0]}"
    return None


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


def _bare_env_dump(args: list[str]) -> bool:
    return (
        bool(args)
        and os.path.basename(args[0]) == "env"
        and not any(arg in ("--help", "--version") for arg in args[1:])
        and _command_start_index(args) is None
    )


def _temporary_executable(args: list[str]) -> tuple[str, str] | None:
    index = _command_start_index(args)
    if index is None:
        return None
    token = args[index]
    candidate = (
        shutil.which(token)
        if os.path.sep not in token
        else os.path.abspath(token)
    )
    if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        return None
    real_candidate = os.path.realpath(candidate)
    roots = {
        os.path.realpath("/tmp"),
        os.path.realpath("/var/tmp"),
        os.path.realpath(tempfile.gettempdir()),
    }
    if not any(
        os.path.commonpath([root, real_candidate]) == root
        for root in roots
    ):
        return None
    try:
        with open(real_candidate, "rb") as file:
            digest = hashlib.sha256(file.read()).hexdigest()
    except OSError:
        return None
    return real_candidate, digest


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


def _effective_chmod_args(args: list[str]) -> list[str]:
    command = shlex.join(args)
    if command_name(command) != "chmod":
        return []
    parts = command_parts(command)
    index = _command_start_index(parts)
    if index is None:
        return []
    return parts[index + 1:]


def _is_chmod_short_option(arg: str) -> bool:
    return (
        len(arg) > 1
        and arg.startswith("-")
        and not arg.startswith("--")
        and set(arg[1:]).issubset(_CHMOD_SHORT_OPTIONS)
    )


def _chmod_targets(args: list[str]) -> list[str]:
    """Return chmod target operands, excluding its mode and option values."""
    chmod_args = _effective_chmod_args(args)
    if not chmod_args:
        return []

    uses_reference = False
    index = 0
    while index < len(chmod_args):
        arg = chmod_args[index]
        if arg == "--":
            index += 1
            if uses_reference:
                return chmod_args[index:]
            if index >= len(chmod_args):
                return []
            return chmod_args[index + 1:]
        if arg == "--reference":
            uses_reference = True
            index += 2
            continue
        if arg.startswith("--reference="):
            uses_reference = True
            index += 1
            continue
        if arg.startswith("--") or _is_chmod_short_option(arg):
            index += 1
            continue
        if uses_reference:
            return [item for item in chmod_args[index:] if item != "--"]
        return [item for item in chmod_args[index + 1:] if item != "--"]
    return []


def _recent_chmod_targets(
    args: list[str],
    *,
    now: float | None = None,
) -> list[str]:
    now = time.time() if now is None else now
    recent = []
    for target in _chmod_targets(args):
        try:
            modified_at = os.stat(target).st_mtime
        except OSError:
            continue
        if now - modified_at <= RECENT_CHMOD_WINDOW_SECONDS:
            recent.append(target)
    return recent


def _require_recent_chmod_confirmation(
    args: list[str],
    *,
    confirmed: bool,
) -> None:
    recent = _recent_chmod_targets(args)
    if confirmed or not recent:
        return

    command = shlex.join(args)
    audit_log(
        "blocked",
        "cmd",
        command,
        "chmod targets a recently created or edited file",
    )
    targets = shlex.join(recent)
    click.echo(f"ozm: blocked chmod for recently created or edited file(s): {targets}", err=True)
    click.echo(
        "ozm: Do not use chmod only to prepare a script for 'ozm run'. "
        "ozm runs a private executable snapshot; the source file only needs a shebang.",
        err=True,
    )
    click.echo(
        "ozm: If the file mode change is intentional, re-run with "
        f"{CONFIRM_RECENT_CHMOD_FLAG} before chmod:",
        err=True,
    )
    click.echo(
        "ozm cmd --confirm-recent-chmod --agent-name \"<work>\" "
        "--agent-description \"<intent>\" chmod ...",
        err=True,
    )
    sys.exit(BLOCKED)


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


def _script_shebang(path: str, interpreter: str | None = None) -> str | None:
    if not os.path.isfile(path):
        return None
    if interpreter is not None:
        return f"#!/usr/bin/env {interpreter}"
    _, ext = os.path.splitext(path)
    inferred = EXTENSION_SHEBANGS.get(ext.lower())
    if inferred:
        return inferred
    try:
        with open(path, "rb") as file:
            first_line = file.readline(256).decode("utf-8", errors="replace").strip()
    except OSError:
        return None
    return first_line if first_line.startswith("#!") else None


def _find_script_in_args(
    args: tuple[str, ...],
) -> tuple[str, str, tuple[str, ...]] | None:
    """Return the script, suggested shebang, and script arguments."""
    interpreter = None
    saw_wrapper = False
    for index, arg in enumerate(args):
        if arg.startswith("-"):
            if interpreter and arg == "-m":
                return None
            continue
        if arg in WRAPPERS and interpreter is None:
            saw_wrapper = True
            continue
        basename = os.path.basename(arg)
        if interpreter is None and basename in INTERPRETERS:
            interpreter = basename
            continue
        if interpreter is not None:
            shebang = _script_shebang(arg, interpreter)
            if shebang:
                return arg, shebang, args[index + 1:]
            return None
        shebang = _script_shebang(arg)
        if shebang and (index == 0 or saw_wrapper):
            return arg, shebang, args[index + 1:]
        # Non-wrapper, non-interpreter, non-script token (e.g. "pytest") —
        # everything after this is arguments to that command, not scripts.
        return None
    return None


def _native_gh_suggestion(agent, operation_args: list[str]) -> str:
    return shlex.join([
        "ozm",
        "gh",
        "--agent-name",
        agent.name,
        "--agent-description",
        agent.description,
        *operation_args,
    ])


def _reject_supported_raw_github_write(
    args: list[str],
    agent,
    *,
    audit_kind: str,
) -> None:
    if not args or os.path.basename(args[0]) != "gh":
        return
    normalized_args = ["gh", *args[1:]]
    operation = match_supported_raw_write(normalized_args)
    if operation is None:
        return

    command = shlex.join(normalized_args)
    suggestion = _native_gh_suggestion(agent, operation.typed_args())
    typed_name = " ".join(operation.typed_args()[:2])
    raw_label = (
        "review-reply"
        if operation.operation_name == "pr.review-reply"
        else operation.operation_name
    )
    reason_label = (
        "pr review-reply"
        if operation.operation_name == "pr.review-reply"
        else typed_name
    )
    audit_log(
        "blocked",
        audit_kind,
        command,
        f"raw {raw_label} POST must use {reason_label}",
    )
    click.echo(
        f"ozm: raw {raw_label} POST is not allowed because Ozm supports "
        f"'{typed_name}'.",
        err=True,
    )
    click.echo(f"ozm: re-run as: {suggestion}", err=True)
    raise click_error(f"use the typed 'ozm gh {typed_name}' operation", BLOCKED)


def _validate_github_proxy_args(
    args: list[str],
    agent,
) -> ReviewReplyOperation | AddSubIssueOperation | None:
    _reject_supported_raw_github_write(args, agent, audit_kind="gh")
    if args and args[0] == "gh":
        return parse_typed_operation(args[1:])
    return None


def _reject_gh_via_cmd(args: list[str], agent) -> None:
    if not args or os.path.basename(args[0]) != "gh":
        return
    normalized_args = ["gh", *args[1:]]
    _reject_supported_raw_github_write(normalized_args, agent, audit_kind="cmd")
    command = shlex.join(normalized_args)
    suggestion = _native_gh_suggestion(agent, normalized_args[1:])
    audit_log("blocked", "cmd", command, "use the native ozm gh command")
    click.echo(
        "ozm: direct GitHub CLI commands must use 'ozm gh', not 'ozm cmd gh'.",
        err=True,
    )
    click.echo(f"ozm: re-run as: {suggestion}", err=True)
    raise click_error("use the native 'ozm gh' command", BLOCKED)


def _cmd_impl(
    confirm_recent_chmod: bool,
    command_and_args: tuple[str, ...],
    *,
    github_proxy: bool = False,
) -> None:
    """Apply command policy and execute one argv-style command."""
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

    typed_operation = None
    if github_proxy:
        typed_operation = _validate_github_proxy_args(args, agent)
    else:
        _reject_gh_via_cmd(args, agent)

    if _bare_env_dump(args):
        command = shlex.join(args)
        audit_log("blocked", "cmd", command, "bare env can expose secrets")
        click.echo(
            "ozm: bare 'env' output is not allowed because it can expose "
            "credentials and tokens. Inspect only a named variable with a "
            "purpose-specific command.",
            err=True,
        )
        sys.exit(BLOCKED)

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
        script, shebang, script_args = match
        suggestion = shlex.join([
            "ozm",
            "run",
            "--agent-name",
            agent.name,
            "--agent-description",
            agent.description,
            script,
            *script_args,
        ])
        audit_log(
            "blocked",
            "cmd",
            shlex.join(args),
            "script content must use ozm run",
        )
        click.echo(
            f"ozm: use '{suggestion}' instead — make sure the script has a "
            f"shebang ({shebang})",
            err=True,
        )
        sys.exit(BLOCKED)

    temporary = _temporary_executable(args)
    if temporary is not None:
        path, digest = temporary
        command = shlex.join(args)
        violation = f"temporary executable {path} (sha256:{digest})"
        if not reason:
            audit_log("blocked", "cmd", command, violation)
            click.echo(
                f"ozm: blocked temporary executable: {path}",
                err=True,
            )
            click.echo(f"ozm: reviewed digest: sha256:{digest}", err=True)
            click.echo(
                "ozm: install it in a trusted location, or use --reason with "
                "its provenance and checksum for a one-time override",
                err=True,
            )
            sys.exit(BLOCKED)
        approval = request_override(command, violation, reason, agent)
        if approval.approved is True:
            if _temporary_executable(args) != temporary:
                audit_log(
                    "blocked",
                    "cmd",
                    command,
                    "temporary executable changed after approval",
                )
                raise click_error(
                    "temporary executable changed after approval and was not run",
                    BLOCKED,
                )
            audit_log("override", "cmd", command, approval.feedback)
            click.echo("ozm: temporary executable override granted (one-time)", err=True)
            result = _run_command(args)
            sys.exit(result.returncode)
        if approval.approved is False:
            audit_log("denied", "cmd", command, approval.feedback)
            click.echo("ozm: temporary executable override denied", err=True)
            sys.exit(DENIED)
        audit_log("no-dialog", "cmd", command, approval.feedback)
        raise click_error(
            "temporary executable approval dialog could not be displayed",
            NO_DIALOG,
        )

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
    audit_kind = "gh" if github_proxy else "cmd"
    disallowed = disallowed_command_reason(command)
    if disallowed:
        audit_log("blocked", audit_kind, command)
        click.echo(f"ozm: blocked command '{command_name(command)}'", err=True)
        click.echo(f"ozm: {disallowed}", err=True)
        sys.exit(BLOCKED)

    _require_recent_chmod_confirmation(args, confirmed=confirm_recent_chmod)

    try:
        blocked = is_command_blocked(command)
    except (OSError, RuntimeError) as exc:
        audit_log("error", audit_kind, command, str(exc))
        raise click_error(
            f"config error: {exc}. The command was NOT executed.",
            CONFIG_ERROR,
        ) from exc
    if blocked:
        if not reason:
            audit_log("blocked", audit_kind, command)
            click.echo(f"ozm: blocked by pattern '{blocked}' in config", err=True)
            click.echo("ozm: use --reason \"justification\" to request a one-time override", err=True)
            sys.exit(BLOCKED)
        approval = request_override(command, f"blocked by pattern '{blocked}'", reason, agent)
        if approval.approved is True:
            audit_log("override", audit_kind, command, approval.feedback)
            click.echo("ozm: override granted (one-time)", err=True)
            result = _run_command(args)
            sys.exit(result.returncode)
        else:
            audit_log("denied", audit_kind, command, approval.feedback)
            click.echo("ozm: override denied", err=True)
            sys.exit(DENIED)

    if typed_operation is not None:
        try:
            operation_allowed = github_operation_allowed(
                typed_operation.operation_name,
                typed_operation.repository,
            )
        except (OSError, RuntimeError) as exc:
            audit_log("error", audit_kind, command, str(exc))
            raise click_error(
                f"config error: {exc}. The command was NOT executed.",
                CONFIG_ERROR,
            ) from exc
        if operation_allowed:
            operation_reason = (
                f"github {typed_operation.operation_name} for "
                f"{typed_operation.repository}"
            )
            audit_log("operation", "gh", command, operation_reason)
            click.echo(f"ozm: allowed ({operation_reason})", err=True)
            result = _run_command(args)
            sys.exit(result.returncode)

    semantic_reason = _builtin_read_only_reason(args)
    if not semantic_reason:
        semantic_reason = _safe_read_only_reason(command)
    if not semantic_reason:
        semantic_reason = github_api_read_only_reason(args)
    if semantic_reason:
        audit_log("semantic", audit_kind, command, semantic_reason)
        click.echo(f"ozm: allowed ({semantic_reason})", err=True)
        result = _run_command(args)
        sys.exit(result.returncode)

    try:
        allowed = is_command_allowed(command)
    except (OSError, RuntimeError) as exc:
        audit_log("error", audit_kind, command, str(exc))
        raise click_error(
            f"config error: {exc}. The command was NOT executed.",
            CONFIG_ERROR,
        ) from exc
    if allowed:
        audit_log("config", audit_kind, command)
        click.echo("ozm: allowed (config)", err=True)
        result = _run_command(args)
        sys.exit(result.returncode)

    key = project_key(CMD_PREFIX + command)
    current_hash = _cmd_hash(command)
    try:
        hashes = load_hashes()
    except (OSError, RuntimeError) as exc:
        audit_log("error", audit_kind, command, str(exc))
        raise click_error(
            f"approval cache error: {exc}. The command was NOT executed.",
            CONFIG_ERROR,
        ) from exc

    if hashes.get(key) == current_hash:
        audit_log("cached", audit_kind, command)
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
        if github_proxy:
            if not run_args or run_args[0] != "gh":
                audit_log("blocked", "gh", run_command, "ozm gh edit removed gh")
                raise click_error(
                    "an edited 'ozm gh' command must remain a GitHub CLI command",
                    BLOCKED,
                )
            _validate_github_proxy_args(run_args, agent)
        else:
            _reject_gh_via_cmd(run_args, agent)
        run_disallowed = disallowed_command_reason(run_command)
        if run_disallowed:
            audit_log("blocked", audit_kind, run_command)
            click.echo(f"ozm: blocked command '{command_name(run_command)}'", err=True)
            click.echo(f"ozm: {run_disallowed}", err=True)
            sys.exit(BLOCKED)
        if run_command != command:
            recheck = is_command_blocked(run_command)
            if recheck:
                audit_log("blocked", audit_kind, run_command)
                click.echo(f"ozm: edited command blocked by pattern '{recheck}'", err=True)
                sys.exit(BLOCKED)
            _require_recent_chmod_confirmation(
                run_args,
                confirmed=confirm_recent_chmod,
            )
        allow_pattern = approval.allow_pattern
        if approval.apply_globally and not allow_pattern:
            allow_pattern = run_command
        if allow_pattern:
            scope = _scope_label(approval.apply_globally)
            try:
                added = add_allowed_command(allow_pattern, global_scope=approval.apply_globally)
            except (OSError, RuntimeError) as exc:
                audit_log("error", audit_kind, run_command, str(exc))
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
            audit_log("error", audit_kind, run_command, str(exc))
            raise click_error(
                f"could not save approval cache: {exc}. The command was NOT executed.",
                CONFIG_ERROR,
            ) from exc
        audit_log("clicked", audit_kind, run_command, approval.feedback)
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
                audit_log("error", audit_kind, command, str(exc))
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
        audit_log("denied", audit_kind, command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: denied cmd — [user] {approval.feedback}", err=True)
        else:
            click.echo("ozm: denied cmd", err=True)
        sys.exit(DENIED)

    audit_log("no-dialog", audit_kind, command, approval.feedback)
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


@click.command(
    "cmd",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option(
    CONFIRM_RECENT_CHMOD_FLAG,
    is_flag=True,
    help="Confirm an intentional chmod of a file edited in the last 10 minutes.",
)
@click.argument("command_and_args", nargs=-1, type=click.UNPROCESSED, required=True)
def cmd_cmd(confirm_recent_chmod: bool, command_and_args: tuple[str, ...]) -> None:
    """Run an arbitrary command after approval.

    A chmod of a recently created or edited file requires
    --confirm-recent-chmod. Do not use chmod to prepare a script for ozm run;
    ozm run executes a private executable snapshot automatically. GitHub CLI
    commands are rejected with an equivalent ozm gh suggestion.
    """
    _cmd_impl(confirm_recent_chmod, command_and_args)

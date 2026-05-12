#!/usr/bin/env python3
"""Git pass-through with rule enforcement on commit and push."""

import re
import shlex
import subprocess
import sys

import click

from ozm.agent import AgentMetadata, extract_agent_metadata
from ozm.approve import request_override
from ozm.audit import log as audit_log
from ozm.config import commit_config

MAX_SUBJECT_LENGTH = 72
MAX_MESSAGE_LENGTH = 500
PROTECTED_BRANCHES = {"main", "master"}
DANGEROUS_SUBCOMMANDS = {"filter-branch", "filter-repo"}
ATTRIBUTION_PATTERN = re.compile(r"^Co-Authored-By:", re.IGNORECASE | re.MULTILINE)
MESSAGE_POLICY_ERROR = 'Commit messages must use a single-line -m "message"'
MESSAGE_SOURCE_FLAGS = {
    "-F",
    "--file",
    "-C",
    "-c",
    "--reuse-message",
    "--reedit-message",
    "--template",
    "--fixup",
    "--squash",
}
MESSAGE_SOURCE_PREFIXES = (
    "--file=",
    "--reuse-message=",
    "--reedit-message=",
    "--template=",
    "--fixup=",
    "--squash=",
)
GLOBAL_FLAGS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--super-prefix",
    "--config-env",
}
GLOBAL_VALUE_PREFIXES = (
    "--git-dir=",
    "--work-tree=",
    "--namespace=",
    "--exec-path=",
    "--super-prefix=",
    "--config-env=",
)
GLOBAL_FLAGS_WITHOUT_VALUE = {
    "--bare",
    "--no-pager",
    "--paginate",
    "--no-replace-objects",
    "--literal-pathspecs",
    "--no-optional-locks",
}


def get_current_branch(global_args: list[str] | None = None) -> str | None:
    result = subprocess.run(
        ["git", *(global_args or []), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _collect_messages(args: list[str]) -> tuple[list[str], bool]:
    messages = []
    has_external_source = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-m", "--message"):
            if i + 1 < len(args):
                messages.append(args[i + 1])
                i += 2
                continue
            messages.append("")
        elif arg.startswith("-m") and len(arg) > 2:
            messages.append(arg[2:])
        elif arg.startswith("--message="):
            messages.append(arg.split("=", 1)[1])
        elif arg in MESSAGE_SOURCE_FLAGS or arg.startswith(MESSAGE_SOURCE_PREFIXES):
            has_external_source = True
            if arg in MESSAGE_SOURCE_FLAGS and i + 1 < len(args):
                i += 2
                continue
        i += 1
    return messages, has_external_source


def extract_message(args: list[str]) -> str | None:
    messages, _ = _collect_messages(args)
    if messages:
        return messages[0]
    for i, arg in enumerate(args):
        if arg in ("-m", "--message") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("-m") and len(arg) > 2:
            return arg[2:]
        if arg.startswith("--message="):
            return arg.split("=", 1)[1]
    return None


def validate_message(message: str) -> list[str]:
    errors = []
    lines = message.splitlines()
    subject = lines[0] if lines else ""

    if len(lines) > 1:
        errors.append("Multi-line commit messages are not allowed — use a single-line -m \"message\"")

    if len(subject) > MAX_SUBJECT_LENGTH:
        errors.append(
            f"Subject line is {len(subject)} chars (max {MAX_SUBJECT_LENGTH})"
        )

    if len(message) > MAX_MESSAGE_LENGTH:
        errors.append(
            f"Total message is {len(message)} chars (max {MAX_MESSAGE_LENGTH})"
        )

    return errors


def _check_commit(args: list[str], global_args: list[str] | None = None) -> str | None:
    """Return a violation string if blocked, None if ok."""
    messages, has_external_source = _collect_messages(args)
    errors = []
    message = messages[0] if len(messages) == 1 else None

    if has_external_source or len(messages) != 1:
        errors.append(MESSAGE_POLICY_ERROR)

    if message is not None:
        errors.extend(validate_message(message))

    cfg = commit_config()

    combined_message = "\n\n".join(messages)
    if cfg.get("allow_attribution") is False and ATTRIBUTION_PATTERN.search(combined_message):
        errors.append("Co-Authored-By attribution is not allowed in this project")

    if errors:
        return "; ".join(errors)

    branch = get_current_branch(global_args)

    if cfg.get("require_branch") and branch in PROTECTED_BRANCHES:
        return f"committing directly to '{branch}' is not allowed"

    prefixes = cfg.get("branch_prefixes")
    if isinstance(prefixes, list) and prefixes and branch:
        if branch not in PROTECTED_BRANCHES and not any(
            branch.startswith(p) for p in prefixes
        ):
            return f"branch '{branch}' does not match required prefixes: {', '.join(prefixes)}"

    return None


def _check_push(args: list[str], global_args: list[str] | None = None) -> str | None:
    """Return a violation string if blocked, None if ok."""
    force_flags = {"-f", "--mirror"}
    if any(a in force_flags or a.startswith("--force") for a in args):
        return "force push is not allowed"

    branch = get_current_branch(global_args)
    if branch in PROTECTED_BRANCHES:
        return f"pushing to '{branch}' is not allowed"

    for arg in args:
        if arg.startswith("-"):
            continue
        targets = [arg]
        if ":" in arg:
            targets.append(arg.split(":", 1)[1])
        for t in targets:
            name = t.removeprefix("+").removeprefix("refs/heads/")
            if name in PROTECTED_BRANCHES:
                return f"pushing to '{name}' is not allowed"

    return None


def _split_global_options(args: list[str]) -> tuple[list[str], list[str]]:
    """Split leading git global options from subcommand args."""
    global_args = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in GLOBAL_FLAGS_WITHOUT_VALUE:
            global_args.append(arg)
            i += 1
            continue
        if arg in GLOBAL_FLAGS_WITH_VALUE:
            if i + 1 >= len(args):
                break
            global_args.extend([arg, args[i + 1]])
            i += 2
            continue
        if arg.startswith(GLOBAL_VALUE_PREFIXES):
            global_args.append(arg)
            i += 1
            continue
        break
    return global_args, args[i:]


def _global_config_key(entry: str) -> str:
    if entry.startswith("--config-env="):
        entry = entry.split("=", 1)[1]
    return entry.split("=", 1)[0]


def _check_global_options(global_args: list[str]) -> str | None:
    i = 0
    while i < len(global_args):
        arg = global_args[i]
        entry = None
        if arg == "-c" and i + 1 < len(global_args):
            entry = global_args[i + 1]
            i += 2
        elif arg == "--config-env" and i + 1 < len(global_args):
            entry = global_args[i + 1]
            i += 2
        elif arg.startswith("--config-env="):
            entry = arg
            i += 1
        else:
            i += 1
        if entry:
            key = _global_config_key(entry)
            if key.startswith("alias.") or key == "core.hooksPath":
                return f"git -c {key} is not allowed"
    return None


def _git_command(args: list[str]) -> str:
    return shlex.join(["git", *args])


def _extract_reason(args: list[str]) -> tuple[list[str], str | None]:
    """Extract --reason from args, return (remaining_args, reason)."""
    cleaned = []
    reason = None
    i = 0
    while i < len(args):
        if args[i] == "--reason" and i + 1 < len(args):
            reason = args[i + 1]
            i += 2
        elif args[i].startswith("--reason="):
            reason = args[i].split("=", 1)[1]
            i += 1
        else:
            cleaned.append(args[i])
            i += 1
    return cleaned, reason


def _handle_violation(
    violation: str,
    command: str,
    reason: str | None,
    agent: AgentMetadata,
) -> None:
    """Block or show override dialog. Exits on block/deny."""
    if not reason:
        click.echo(f"ozm: {violation}", err=True)
        click.echo("ozm: use --reason \"justification\" to request a one-time override", err=True)
        sys.exit(1)

    approval = request_override(command, violation, reason, agent)

    if approval.approved is True:
        audit_log("override", "git", command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: override granted — {approval.feedback}", err=True)
        else:
            click.echo("ozm: override granted (one-time)", err=True)
        return

    if approval.approved is False:
        audit_log("denied", "git", command, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: override denied — {approval.feedback}", err=True)
        else:
            click.echo("ozm: override denied", err=True)
        sys.exit(1)

    audit_log("no-dialog", "git", command)
    click.echo(
        "ozm: BLOCKED — approval dialog could not be displayed. "
        "Do NOT retry.",
        err=True,
    )
    sys.exit(1)


@click.command(
    "git",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def git_cmd(args: tuple[str, ...]) -> None:
    """Git pass-through. Enforces rules on commit and push."""
    args_list, agent = extract_agent_metadata(list(args))
    if not args_list:
        subprocess.run(["git"])
        return

    global_args, command_args = _split_global_options(args_list)
    global_violation = _check_global_options(global_args)
    if global_violation:
        _handle_violation(global_violation, _git_command(args_list), None, agent)

    if not command_args:
        result = subprocess.run(["git", *global_args])
        sys.exit(result.returncode)

    subcmd = command_args[0]
    rest = list(command_args[1:])
    rest, reason = _extract_reason(rest)

    if subcmd in DANGEROUS_SUBCOMMANDS:
        full_cmd = _git_command([*global_args, subcmd, *rest])
        _handle_violation(f"'git {subcmd}' is not allowed", full_cmd, reason, agent)

    if subcmd == "config":
        for arg in rest:
            if arg.startswith("alias.") or arg.startswith("core.hooksPath"):
                full_cmd = _git_command([*global_args, subcmd, *rest])
                _handle_violation(f"'git config {arg}' is not allowed", full_cmd, reason, agent)

    if subcmd == "commit":
        violation = _check_commit(rest, global_args)
        if violation:
            full_cmd = _git_command([*global_args, subcmd, *rest])
            _handle_violation(violation, full_cmd, reason, agent)

    elif subcmd == "push":
        violation = _check_push(rest, global_args)
        if violation:
            full_cmd = _git_command([*global_args, subcmd, *rest])
            _handle_violation(violation, full_cmd, reason, agent)

    result = subprocess.run(["git", *global_args, subcmd, *rest])
    sys.exit(result.returncode)

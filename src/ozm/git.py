#!/usr/bin/env python3
"""Git pass-through with rule enforcement on commit and push."""

import re
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


def get_current_branch() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def extract_message(args: list[str]) -> str | None:
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


def _check_commit(args: list[str]) -> str | None:
    """Return a violation string if blocked, None if ok."""
    message = extract_message(args)
    if message:
        errors = validate_message(message)

        cfg = commit_config()

        if cfg.get("allow_attribution") is False and ATTRIBUTION_PATTERN.search(message):
            errors.append("Co-Authored-By attribution is not allowed in this project")

        if errors:
            return "; ".join(errors)

    cfg = commit_config()
    branch = get_current_branch()

    if cfg.get("require_branch") and branch in PROTECTED_BRANCHES:
        return f"committing directly to '{branch}' is not allowed"

    prefixes = cfg.get("branch_prefixes")
    if isinstance(prefixes, list) and prefixes and branch:
        if branch not in PROTECTED_BRANCHES and not any(
            branch.startswith(p) for p in prefixes
        ):
            return f"branch '{branch}' does not match required prefixes: {', '.join(prefixes)}"

    return None


def _check_push(args: list[str]) -> str | None:
    """Return a violation string if blocked, None if ok."""
    force_flags = {"-f", "--mirror"}
    if any(a in force_flags or a.startswith("--force") for a in args):
        return "force push is not allowed"

    branch = get_current_branch()
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

    subcmd = args_list[0]
    rest = list(args_list[1:])
    rest, reason = _extract_reason(rest)

    if subcmd in DANGEROUS_SUBCOMMANDS:
        full_cmd = f"git {subcmd} {' '.join(rest)}"
        _handle_violation(f"'git {subcmd}' is not allowed", full_cmd, reason, agent)

    if subcmd == "config":
        for arg in rest:
            if arg.startswith("alias.") or arg.startswith("core.hooksPath"):
                full_cmd = f"git {subcmd} {' '.join(rest)}"
                _handle_violation(f"'git config {arg}' is not allowed", full_cmd, reason, agent)

    if subcmd == "commit":
        violation = _check_commit(rest)
        if violation:
            full_cmd = f"git {subcmd} {' '.join(rest)}"
            _handle_violation(violation, full_cmd, reason, agent)

    elif subcmd == "push":
        violation = _check_push(rest)
        if violation:
            full_cmd = f"git {subcmd} {' '.join(rest)}"
            _handle_violation(violation, full_cmd, reason, agent)

    result = subprocess.run(["git", subcmd, *rest])
    sys.exit(result.returncode)

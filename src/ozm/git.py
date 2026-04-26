#!/usr/bin/env python3
"""Git pass-through with rule enforcement on commit and push."""

import re
import subprocess
import sys

import click

from ozm.config import commit_config

MAX_SUBJECT_LENGTH = 72
MAX_MESSAGE_LENGTH = 500
PROTECTED_BRANCHES = {"main", "master"}
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

    if len(subject) > MAX_SUBJECT_LENGTH:
        errors.append(
            f"Subject line is {len(subject)} chars (max {MAX_SUBJECT_LENGTH})"
        )

    if len(message) > MAX_MESSAGE_LENGTH:
        errors.append(
            f"Total message is {len(message)} chars (max {MAX_MESSAGE_LENGTH})"
        )

    return errors


def _check_commit(args: list[str]) -> None:
    message = extract_message(args)
    if message:
        errors = validate_message(message)

        cfg = commit_config()

        if cfg.get("allow_attribution") is False and ATTRIBUTION_PATTERN.search(message):
            errors.append("Co-Authored-By attribution is not allowed in this project")

        if errors:
            click.echo("ozm: commit blocked:", err=True)
            for e in errors:
                click.echo(f"  - {e}", err=True)
            sys.exit(1)

    cfg = commit_config()
    branch = get_current_branch()

    if cfg.get("require_branch") and branch in PROTECTED_BRANCHES:
        click.echo(
            f"ozm: commit blocked: committing directly to '{branch}' is not allowed",
            err=True,
        )
        sys.exit(1)

    prefixes = cfg.get("branch_prefixes")
    if isinstance(prefixes, list) and prefixes and branch:
        if branch not in PROTECTED_BRANCHES and not any(
            branch.startswith(p) for p in prefixes
        ):
            click.echo(
                f"ozm: commit blocked: branch '{branch}' does not match "
                f"required prefixes: {', '.join(prefixes)}",
                err=True,
            )
            sys.exit(1)


def _check_push(args: list[str]) -> None:
    force_flags = {"--force", "-f"}
    if any(a in force_flags for a in args):
        click.echo("ozm: force push is not allowed", err=True)
        sys.exit(1)

    branch = get_current_branch()
    if branch in PROTECTED_BRANCHES:
        click.echo(f"ozm: pushing to '{branch}' is not allowed", err=True)
        sys.exit(1)

    for arg in args:
        if arg.startswith("-"):
            continue
        targets = [arg]
        if ":" in arg:
            targets.append(arg.split(":", 1)[1])
        for t in targets:
            name = t.removeprefix("refs/heads/")
            if name in PROTECTED_BRANCHES:
                click.echo(f"ozm: pushing to '{name}' is not allowed", err=True)
                sys.exit(1)


@click.command(
    "git",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def git_cmd(args: tuple[str, ...]) -> None:
    """Git pass-through. Enforces rules on commit and push."""
    if not args:
        subprocess.run(["git"])
        return

    subcmd = args[0]
    rest = list(args[1:])

    if subcmd == "commit":
        _check_commit(rest)
    elif subcmd == "push":
        _check_push(rest)

    result = subprocess.run(["git", *args])
    sys.exit(result.returncode)

#!/usr/bin/env python3
"""Git command wrappers with rule enforcement."""

import subprocess
import sys

import click

MAX_SUBJECT_LENGTH = 72
MAX_MESSAGE_LENGTH = 500
PROTECTED_BRANCHES = {"main", "master"}


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


@click.group("git")
def git_group():
    """Git command wrappers with rule enforcement."""


@git_group.command(
    "commit",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def commit(args: tuple[str, ...]) -> None:
    """Run git commit with message validation."""
    message = extract_message(list(args))
    if message:
        errors = validate_message(message)
        if errors:
            click.echo("ozm: commit blocked:", err=True)
            for e in errors:
                click.echo(f"  - {e}", err=True)
            sys.exit(1)

    result = subprocess.run(["git", "commit", *args])
    sys.exit(result.returncode)


@git_group.command(
    "push",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def push(args: tuple[str, ...]) -> None:
    """Run git push with safety checks."""
    args_list = list(args)

    force_flags = {"--force", "-f"}
    if any(a in force_flags for a in args_list):
        click.echo("ozm: force push is not allowed", err=True)
        sys.exit(1)

    branch = get_current_branch()
    if branch in PROTECTED_BRANCHES:
        click.echo(f"ozm: pushing to '{branch}' is not allowed", err=True)
        sys.exit(1)

    for arg in args_list:
        if not arg.startswith("-") and arg in PROTECTED_BRANCHES:
            click.echo(f"ozm: pushing to '{arg}' is not allowed", err=True)
            sys.exit(1)

    result = subprocess.run(["git", "push", *args_list])
    sys.exit(result.returncode)

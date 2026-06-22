#!/usr/bin/env python3
"""Reviewed shell snippets for integrations that need pipes/redirection."""

import sys

import click

from ozm.agent import extract_agent_metadata
from ozm.run import SHELL_PREFIX, run_stdin_content


def _script_for_bash(content: str) -> str:
    if content.startswith("#!"):
        script = content
    else:
        script = f"#!/usr/bin/env bash\n{content}"
    if not script.endswith("\n"):
        script += "\n"
    return script


@click.command(
    "shell",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--command", "command_text", help="Shell command text to review and run.")
@click.option("-c", "command_text_short", help="Alias for --command.")
@click.option("--title", help="Stable title for approval cache entries.")
@click.argument("items", nargs=-1, type=click.UNPROCESSED, required=False)
def shell_cmd(
    command_text: str | None,
    command_text_short: str | None,
    title: str | None,
    items: tuple[str, ...],
) -> None:
    """Review and run raw bash supplied by --command or stdin."""
    parts, agent = extract_agent_metadata(list(items))
    if command_text is not None and command_text_short is not None:
        raise click.ClickException("Use only one of --command or -c.")
    content = command_text if command_text is not None else command_text_short
    if content is None:
        content = sys.stdin.read()
    if not content:
        raise click.ClickException("shell command is empty")
    run_stdin_content(
        _script_for_bash(content),
        tuple(parts),
        agent,
        title=title or "shell-command",
        key_prefix=SHELL_PREFIX,
        display_prefix="shell",
    )

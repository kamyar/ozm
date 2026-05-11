#!/usr/bin/env python3
"""Agent-provided context for ozm approvals."""

import shlex
from dataclasses import dataclass

import click

MAX_AGENT_NAME_LENGTH = 80
MAX_AGENT_DESCRIPTION_LENGTH = 200

MEMORY_REMINDER = (
    "ozm: Before retrying, write this requirement to your memory: every "
    "ozm run/cmd/git invocation must include "
    "--agent-name \"<what you are working on>\" and "
    "--agent-description \"<one-line intent>\"."
)


@dataclass(frozen=True)
class AgentMetadata:
    name: str
    description: str


def _has_newline(value: str) -> bool:
    return "\n" in value or "\r" in value


def _metadata_error(message: str) -> click.ClickException:
    return click.ClickException(f"{message}\n{MEMORY_REMINDER}")


def validate_agent_metadata(
    agent_name: str | None,
    agent_description: str | None,
) -> AgentMetadata:
    """Validate required agent metadata."""
    if agent_name is None or agent_description is None:
        raise _metadata_error(
            "ozm: missing required agent metadata: pass "
            "--agent-name and --agent-description."
        )

    name = agent_name.strip()
    description = agent_description.strip()
    errors = []

    if not name:
        errors.append("--agent-name must not be empty")
    elif _has_newline(name):
        errors.append("--agent-name must be one line")
    elif len(name) > MAX_AGENT_NAME_LENGTH:
        errors.append(
            f"--agent-name is {len(name)} chars "
            f"(max {MAX_AGENT_NAME_LENGTH})"
        )

    if not description:
        errors.append("--agent-description must not be empty")
    elif _has_newline(description):
        errors.append("--agent-description must be exactly one line")
    elif len(description) > MAX_AGENT_DESCRIPTION_LENGTH:
        errors.append(
            f"--agent-description is {len(description)} chars "
            f"(max {MAX_AGENT_DESCRIPTION_LENGTH})"
        )

    if errors:
        raise _metadata_error("ozm: invalid agent metadata: " + "; ".join(errors))

    return AgentMetadata(name=name, description=description)


def extract_agent_metadata(args: list[str]) -> tuple[list[str], AgentMetadata]:
    """Remove ozm agent metadata flags from args and return validated metadata."""
    cleaned = []
    agent_name = None
    agent_description = None
    i = 0

    while i < len(args):
        arg = args[i]

        if arg == "--":
            cleaned.extend(args[i + 1:])
            break

        if arg == "--agent-name":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise _metadata_error("ozm: --agent-name requires a value.")
            agent_name = args[i + 1]
            i += 2
            continue

        if arg.startswith("--agent-name="):
            agent_name = arg.split("=", 1)[1]
            i += 1
            continue

        if arg == "--agent-description":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise _metadata_error("ozm: --agent-description requires a value.")
            agent_description = args[i + 1]
            i += 2
            continue

        if arg.startswith("--agent-description="):
            agent_description = arg.split("=", 1)[1]
            i += 1
            continue

        cleaned.append(arg)
        i += 1

    return cleaned, validate_agent_metadata(agent_name, agent_description)


def extract_agent_metadata_from_command(
    command: str,
) -> tuple[str, AgentMetadata | None]:
    """Extract metadata flags from a shell command string when present."""
    try:
        args = shlex.split(command)
    except ValueError:
        return command, None

    agent_name = None
    agent_description = None
    cleaned = []
    i = 0

    while i < len(args):
        arg = args[i]

        if arg == "--agent-name" and i + 1 < len(args):
            agent_name = args[i + 1]
            i += 2
            continue
        if arg.startswith("--agent-name="):
            agent_name = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--agent-description" and i + 1 < len(args):
            agent_description = args[i + 1]
            i += 2
            continue
        if arg.startswith("--agent-description="):
            agent_description = arg.split("=", 1)[1]
            i += 1
            continue

        cleaned.append(arg)
        i += 1

    if agent_name is None and agent_description is None:
        return command, None

    return (
        shlex.join(cleaned),
        validate_agent_metadata(agent_name, agent_description),
    )

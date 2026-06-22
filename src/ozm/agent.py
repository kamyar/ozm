#!/usr/bin/env python3
"""Agent-provided context for ozm approvals."""

import json
import os
import shlex
from dataclasses import dataclass

import click

from ozm.exit_codes import MISSING_METADATA

MAX_AGENT_NAME_LENGTH = 80
MAX_AGENT_DESCRIPTION_LENGTH = 200

MEMORY_REMINDER = (
    "ozm: Before retrying, write this requirement to your memory: every "
    "ozm run/cmd/git/shell invocation must include agent metadata via "
    "--agent-name/--agent-description, --agent-json, or "
    "OZM_AGENT_NAME/OZM_AGENT_DESCRIPTION."
)


@dataclass(frozen=True)
class AgentMetadata:
    name: str
    description: str


def _has_newline(value: str) -> bool:
    return "\n" in value or "\r" in value


def _metadata_error(message: str) -> click.ClickException:
    exc = click.ClickException(f"{message}\n{MEMORY_REMINDER}")
    exc.exit_code = MISSING_METADATA
    return exc


def _metadata_from_json(raw: str) -> tuple[str | None, str | None]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _metadata_error(f"ozm: --agent-json is not valid JSON: {exc.msg}.") from exc
    if not isinstance(data, dict):
        raise _metadata_error("ozm: --agent-json must be a JSON object.")
    name = data.get("name", data.get("agent_name"))
    description = data.get("description", data.get("agent_description"))
    if name is not None and not isinstance(name, str):
        raise _metadata_error("ozm: --agent-json name must be a string.")
    if description is not None and not isinstance(description, str):
        raise _metadata_error("ozm: --agent-json description must be a string.")
    return name, description


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
    """Remove ozm agent metadata flags from args and return validated metadata.

    Precedence is explicit flags, then --agent-json, then OZM_AGENT_* env vars.
    Missing values can be filled from a lower-precedence source, so passing only
    --agent-name can still use OZM_AGENT_DESCRIPTION.
    """
    cleaned = []
    env_name = os.environ.get("OZM_AGENT_NAME")
    env_description = os.environ.get("OZM_AGENT_DESCRIPTION")
    json_name = None
    json_description = None
    flag_name = None
    flag_description = None
    i = 0

    while i < len(args):
        arg = args[i]

        if arg == "--":
            cleaned.extend(args[i + 1:])
            break

        if arg == "--agent-json":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise _metadata_error("ozm: --agent-json requires a value.")
            json_name, json_description = _metadata_from_json(args[i + 1])
            i += 2
            continue

        if arg.startswith("--agent-json="):
            json_name, json_description = _metadata_from_json(arg.split("=", 1)[1])
            i += 1
            continue

        if arg == "--agent-name":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise _metadata_error("ozm: --agent-name requires a value.")
            flag_name = args[i + 1]
            i += 2
            continue

        if arg.startswith("--agent-name="):
            flag_name = arg.split("=", 1)[1]
            i += 1
            continue

        if arg == "--agent-description":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise _metadata_error("ozm: --agent-description requires a value.")
            flag_description = args[i + 1]
            i += 2
            continue

        if arg.startswith("--agent-description="):
            flag_description = arg.split("=", 1)[1]
            i += 1
            continue

        cleaned.append(arg)
        i += 1

    agent_name = flag_name if flag_name is not None else json_name if json_name is not None else env_name
    agent_description = (
        flag_description
        if flag_description is not None
        else json_description
        if json_description is not None
        else env_description
    )
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

        if arg == "--agent-json" and i + 1 < len(args):
            agent_name, agent_description = _metadata_from_json(args[i + 1])
            i += 2
            continue
        if arg.startswith("--agent-json="):
            agent_name, agent_description = _metadata_from_json(arg.split("=", 1)[1])
            i += 1
            continue
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

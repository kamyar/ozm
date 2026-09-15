#!/usr/bin/env python3
"""Pre-approval routing for commands with safer installed entry points."""

from __future__ import annotations

import os
import shlex

from ozm.agent import AgentMetadata
from ozm.config import _command_start_index


def example_inspect_wrapper_args(argv: list[str]) -> list[str] | None:
    """Return Example inspection arguments when argv uses an unnecessary wrapper."""
    start = _command_start_index(argv)
    if start is None:
        return None
    command = os.path.basename(argv[start])
    rest = argv[start + 1:]

    if command == "example_inspect.py":
        return rest
    if command in {"python", "python3"}:
        if rest[:2] == ["-m", "example_inspect"]:
            return rest[2:]
        return None
    if command == "uv" and rest[:1] == ["run"]:
        for index, token in enumerate(rest[1:], 1):
            if (
                os.path.basename(token) in {"python", "python3"}
                and rest[index + 1:index + 3] == ["-m", "example_inspect"]
            ):
                return rest[index + 3:]
    return None


def example_inspect_suggestion(
    arguments: list[str],
    agent: AgentMetadata,
) -> str:
    return shlex.join([
        "ozm",
        "cmd",
        "--agent-name",
        agent.name,
        "--agent-description",
        agent.description,
        "example_inspect",
        *arguments,
    ])

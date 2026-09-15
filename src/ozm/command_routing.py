#!/usr/bin/env python3
"""Pre-approval routing for private tools with safer installed entry points."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from ozm.agent import AgentMetadata
from ozm.config import _command_start_index
from ozm.rule_packs import EntrypointRedirectRule, load_entrypoint_redirects


@dataclass(frozen=True)
class EntrypointRedirect:
    """A matched local routing rule and arguments for its target entry point."""

    rule: EntrypointRedirectRule
    arguments: tuple[str, ...]

    @property
    def qualified_id(self) -> str:
        return f"{self.rule.source}/{self.rule.rule_id}"


def _python_module_arguments(
    tokens: list[str],
    modules: tuple[str, ...],
) -> tuple[str, ...] | None:
    if len(tokens) >= 2 and tokens[0] == "-m" and tokens[1] in modules:
        return tuple(tokens[2:])
    return None


_UV_RUN_VALUE_OPTIONS = {
    "--config-file",
    "--default-index",
    "--directory",
    "--env-file",
    "--extra",
    "--extra-index-url",
    "--find-links",
    "--index",
    "--index-strategy",
    "--index-url",
    "--keyring-provider",
    "--link-mode",
    "--package",
    "--project",
    "--python",
    "--python-platform",
    "--resolution",
    "--torch-backend",
    "--with",
    "--with-editable",
    "--with-requirements",
    "-p",
}


def _uv_run_command(tokens: list[str]) -> list[str]:
    """Return uv run's command argv without scanning command arguments."""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1:]
        if token in _UV_RUN_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                return []
            index += 2
            continue
        if any(token.startswith(option + "=") for option in _UV_RUN_VALUE_OPTIONS):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return tokens[index:]
    return []


def configured_entrypoint_redirect(
    argv: list[str],
    *,
    rules: tuple[EntrypointRedirectRule, ...] | None = None,
) -> EntrypointRedirect | None:
    """Return the first enabled local entry-point redirect that matches argv."""
    start = _command_start_index(argv)
    if start is None:
        return None
    command = os.path.basename(argv[start])
    rest = argv[start + 1:]

    if rules is None:
        rules = load_entrypoint_redirects()
    for rule in rules:
        if command in rule.script_basenames:
            return EntrypointRedirect(rule=rule, arguments=tuple(rest))
        if command in {"python", "python3"}:
            arguments = _python_module_arguments(rest, rule.python_modules)
            if arguments is not None:
                return EntrypointRedirect(rule=rule, arguments=arguments)
        if command == "uv" and rest[:1] == ["run"]:
            uv_command = _uv_run_command(rest)
            if uv_command and os.path.basename(uv_command[0]) in {"python", "python3"}:
                arguments = _python_module_arguments(
                    uv_command[1:],
                    rule.python_modules,
                )
                if arguments is not None:
                    return EntrypointRedirect(rule=rule, arguments=arguments)
    return None


def entrypoint_suggestion(
    redirect: EntrypointRedirect,
    agent: AgentMetadata,
) -> str:
    """Build a shell-safe direct command for a matched local redirect."""
    return shlex.join([
        "ozm",
        "cmd",
        "--agent-name",
        agent.name,
        "--agent-description",
        agent.description,
        "--",
        redirect.rule.target,
        *redirect.arguments,
    ])

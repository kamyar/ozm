#!/usr/bin/env python3
"""Pre-approval routing for private tools with safer installed entry points."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from ozm.agent import AgentMetadata
from ozm.config import _command_start_index, _is_env_assignment
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
    "--cache-dir",
    "--color",
    "--config-file",
    "--default-index",
    "--directory",
    "--env-file",
    "--exclude",
    "--extra",
    "--extra-index-url",
    "--find-links",
    "--group",
    "--index",
    "--index-strategy",
    "--index-url",
    "--keyring-provider",
    "--link-mode",
    "--no-group",
    "--package",
    "--project",
    "--python",
    "--python-platform",
    "--python-preference",
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


def is_explicit_shebang_script(path: str) -> bool:
    """Identify explicit script paths, not local namesakes of installed commands."""
    if os.path.sep not in path or not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as file:
            return file.read(2) == b"#!"
    except OSError:
        return False


def script_command_argv(argv: list[str]) -> list[str]:
    """Unwrap environment prefixes and uv run without hiding wrapper scripts.

    This is a block-only routing helper, never an authorization classifier.
    Bare uv/env names keep their installed-command routing; execution resolves
    them through PATH, not a namesake file in the working directory. Explicit
    script paths retain content review. Each iteration consumes tokens, so
    nested wrappers need no depth cutoff.
    """
    while argv:
        start = 0
        while start < len(argv) and _is_env_assignment(argv[start]):
            start += 1
        if start < len(argv) and is_explicit_shebang_script(argv[start]):
            return argv[start:]
        start = _command_start_index(argv)
        if start is None:
            return []
        argv = argv[start:]
        if is_explicit_shebang_script(argv[0]):
            return argv
        if os.path.basename(argv[0]) != "uv" or argv[1:2] != ["run"]:
            return argv
        argv = _uv_run_command(argv[1:])
    return []


def configured_entrypoint_redirect(
    argv: list[str],
    *,
    rules: tuple[EntrypointRedirectRule, ...] | None = None,
) -> EntrypointRedirect | None:
    """Return the first enabled local entry-point redirect that matches argv."""
    if rules is None:
        rules = load_entrypoint_redirects()
    argv = script_command_argv(argv)
    if not argv:
        return None
    command = os.path.basename(argv[0])
    rest = argv[1:]

    for rule in rules:
        if rule.target_family == "run" and os.path.abspath(argv[0]) == rule.target:
            continue
        if command in rule.script_basenames:
            return EntrypointRedirect(rule=rule, arguments=tuple(rest))
        if command in {"python", "python3"}:
            arguments = _python_module_arguments(rest, rule.python_modules)
            if arguments is not None:
                return EntrypointRedirect(rule=rule, arguments=arguments)
            script_args = rest[1:] if rest[:1] == ["--"] else rest
            if (
                script_args
                and not script_args[0].startswith("-")
                and os.path.basename(script_args[0]) in rule.script_basenames
            ):
                return EntrypointRedirect(rule=rule, arguments=tuple(script_args[1:]))
    return None


def entrypoint_suggestion(
    redirect: EntrypointRedirect,
    agent: AgentMetadata,
) -> str:
    """Build a shell-safe direct command for a matched local redirect."""
    return shlex.join([
        "ozm",
        redirect.rule.target_family,
        "--agent-name",
        agent.name,
        "--agent-description",
        agent.description,
        "--",
        redirect.rule.target,
        *redirect.arguments,
    ])

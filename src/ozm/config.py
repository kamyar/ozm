#!/usr/bin/env python3
"""Per-project configuration via .ozm.yaml."""

import fnmatch
import hashlib
import os
import sys

import click
import yaml


CONFIG_FILE = ".ozm.yaml"
OZM_DIR = os.path.expanduser("~/.ozm")
TRUST_FILE = os.path.join(OZM_DIR, "trusted_configs.yaml")


def find_project_root() -> str:
    """Walk up from cwd to find directory containing .ozm.yaml or .git, else cwd."""
    d = os.getcwd()
    while True:
        if os.path.exists(os.path.join(d, CONFIG_FILE)) or os.path.exists(
            os.path.join(d, ".git")
        ):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


def load_project_config() -> dict:
    """Load .ozm.yaml from the project root. Returns empty dict if missing."""
    root = find_project_root()
    path = os.path.join(root, CONFIG_FILE)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}


def is_command_allowed(command: str) -> bool:
    """Check if a command matches any pattern in the project's allowed_commands."""
    config = load_project_config()
    patterns = config.get("allowed_commands", [])
    if not isinstance(patterns, list):
        return False
    first_word = command.split()[0] if command.strip() else ""
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(first_word, pattern):
            return True
    return False


def add_allowed_command(pattern: str) -> None:
    """Append a pattern to allowed_commands in .ozm.yaml."""
    root = find_project_root()
    path = os.path.join(root, CONFIG_FILE)
    config = load_project_config()
    commands = config.get("allowed_commands", [])
    if not isinstance(commands, list):
        commands = []
    if pattern not in commands:
        commands.append(pattern)
        config["allowed_commands"] = commands
        with open(path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def commit_config() -> dict:
    """Return the 'commit' section of .ozm.yaml, or empty dict."""
    config = load_project_config()
    section = config.get("commit", {})
    return section if isinstance(section, dict) else {}


def project_key(key: str) -> str:
    """Prefix a hash key with the project root for project-scoped storage."""
    root = find_project_root()
    return f"{root}:{key}"

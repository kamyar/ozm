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


def _config_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_trusted() -> dict[str, str]:
    if os.path.exists(TRUST_FILE):
        with open(TRUST_FILE) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    return {}


def _save_trusted(trusted: dict[str, str]) -> None:
    os.makedirs(OZM_DIR, exist_ok=True)
    with open(TRUST_FILE, "w") as f:
        yaml.dump(trusted, f, default_flow_style=False, sort_keys=True)


def trust_config(path: str) -> None:
    """Mark a config file as trusted at its current hash."""
    trusted = _load_trusted()
    trusted[os.path.abspath(path)] = _config_hash(path)
    _save_trusted(trusted)


def check_config_trust(path: str) -> bool:
    """Return True if the config at path is trusted (hash matches)."""
    abs_path = os.path.abspath(path)
    trusted = _load_trusted()
    stored = trusted.get(abs_path)
    if stored is None:
        return False
    return stored == _config_hash(path)


def _warn_untrusted(path: str) -> dict:
    """Warn about untrusted config. Returns empty dict if user declines trust."""
    if not sys.stdin.isatty():
        click.echo(f"ozm: untrusted config {path} — run interactively to trust", err=True)
        return {}
    click.echo(f"\nozm: new or modified config detected: {path}", err=True)
    with open(path) as f:
        for line in f:
            click.echo(f"  {line}", nl=False, err=True)
    click.echo(err=True)
    try:
        answer = input("ozm: trust this config? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return {}
    if answer in ("y", "yes"):
        trust_config(path)
        click.echo("ozm: config trusted", err=True)
        with open(path) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    click.echo("ozm: config ignored — using defaults", err=True)
    return {}


def _load_raw_config() -> dict:
    """Load .ozm.yaml without trust checks. For internal use only."""
    root = find_project_root()
    path = os.path.join(root, CONFIG_FILE)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}


def load_project_config() -> dict:
    """Load .ozm.yaml from the project root. Returns empty dict if missing or untrusted."""
    root = find_project_root()
    path = os.path.join(root, CONFIG_FILE)
    if not os.path.exists(path):
        return {}
    if not check_config_trust(path):
        return _warn_untrusted(path)
    with open(path) as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}


def is_command_blocked(command: str) -> str | None:
    """Check if a command matches any pattern in blocked_commands. Returns the matching pattern or None."""
    config = _load_raw_config()
    patterns = config.get("blocked_commands", [])
    if not isinstance(patterns, list):
        return None
    first_word = command.split()[0] if command.strip() else ""
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(first_word, pattern):
            return pattern
    return None


def is_command_allowed(command: str) -> bool:
    """Check if a command matches any pattern in the project's allowed_commands."""
    config = _load_raw_config()
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
        trust_config(path)


def commit_config() -> dict:
    """Return the 'commit' section of .ozm.yaml, or empty dict."""
    config = load_project_config()
    section = config.get("commit", {})
    return section if isinstance(section, dict) else {}


def project_key(key: str) -> str:
    """Prefix a hash key with the project root for project-scoped storage."""
    root = find_project_root()
    return f"{root}:{key}"

#!/usr/bin/env python3
"""Per-project configuration from ~/.ozm/projects/.

Config files live exclusively in ~/.ozm/projects/<name>-<hash>.yaml.
In-repo .ozm.yaml is never read at runtime — use `ozm trust` to
snapshot it into ~/.ozm/projects/.
"""

import fnmatch
import hashlib
import os
import re
import unicodedata

import yaml

SHELL_METACHARS = re.compile(r"[;|&$`\n()<>{}\[\]]")


def sanitize_command(command: str) -> str:
    return "".join(c for c in command if unicodedata.category(c) not in ("Cf", "Cc", "Mn") or c in ("\n", "\t"))


OZM_DIR = os.path.expanduser("~/.ozm")
if os.path.islink(OZM_DIR):
    raise RuntimeError(f"~/.ozm is a symlink — refusing to load config (possible tampering)")
PROJECTS_DIR = os.path.join(OZM_DIR, "projects")


def find_project_root() -> str:
    """Walk up from cwd to find directory containing .git, else cwd."""
    d = os.getcwd()
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


def _project_config_path() -> str:
    """Path to the user-owned config for the current project in ~/.ozm/projects/."""
    root = find_project_root()
    slug = hashlib.sha256(root.encode()).hexdigest()[:16]
    name = os.path.basename(root)
    return os.path.join(PROJECTS_DIR, f"{name}-{slug}.yaml")


def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}


def load_project_config() -> dict:
    """Load config from ~/.ozm/projects/. Never reads in-repo files."""
    return _load_yaml(_project_config_path())


def _save_user_config(config: dict) -> None:
    """Save to the user-owned config file in ~/.ozm/projects/."""
    path = _project_config_path()
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def is_command_blocked(command: str) -> str | None:
    """Check if a command matches any pattern in blocked_commands."""
    command = sanitize_command(command)
    config = load_project_config()
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
    command = sanitize_command(command)
    if SHELL_METACHARS.search(command):
        return False
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
    """Append a pattern to allowed_commands in the user-owned config."""
    config = load_project_config()
    commands = config.get("allowed_commands", [])
    if not isinstance(commands, list):
        commands = []
    if pattern not in commands:
        commands.append(pattern)
        config["allowed_commands"] = commands
        _save_user_config(config)


def commit_config() -> dict:
    """Return the 'commit' section of the config."""
    config = load_project_config()
    section = config.get("commit", {})
    return section if isinstance(section, dict) else {}


def project_key(key: str) -> str:
    """Prefix a hash key with the project root for project-scoped storage."""
    root = find_project_root()
    return f"{root}\0{key}"

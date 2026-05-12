#!/usr/bin/env python3
"""Configuration from ~/.ozm/config.yaml and ~/.ozm/projects/.

Config files live exclusively in ~/.ozm/projects/<name>-<hash>.yaml.
In-repo .ozm.yaml is never read at runtime — use `ozm trust` to
snapshot it into ~/.ozm/projects/.
"""

import fnmatch
import hashlib
import os
import re
import shlex
import unicodedata

from ozm.storage import load_yaml_no_follow, refuse_symlink, save_yaml_atomic_no_follow

SHELL_METACHARS = frozenset(";|&$`\n()<>{}[]")

SED_ALTERNATIVES = (
    "sed is disallowed because it can edit files in-place and cannot be safely "
    "blanket-approved. Use rg for searching, cat/nl/head/tail for viewing, or "
    "write a small reviewed script and run it with 'ozm run <script>' for "
    "transformations."
)
RG_PRE_REASON = (
    "rg --pre is disallowed because it executes a preprocessor command. "
    "Use rg without --pre, or put preprocessing in a reviewed script and run it "
    "with 'ozm run <script>'."
)
DISALLOWED_COMMANDS = {
    "sed": SED_ALTERNATIVES,
    "gsed": SED_ALTERNATIVES,
}


def sanitize_command(command: str) -> str:
    return "".join(c for c in command if unicodedata.category(c) not in ("Cf", "Cc", "Mn") or c in ("\n", "\t"))


def has_shell_metacharacters(command: str) -> bool:
    quote = None
    escaped = False
    for ch in command:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            elif quote == '"' and ch in "$`":
                return True
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch in SHELL_METACHARS:
            return True
    return quote is not None


def _is_env_assignment(token: str) -> bool:
    name, sep, _value = token.partition("=")
    return bool(sep and name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def command_parts(command: str) -> list[str]:
    command = sanitize_command(command)
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _command_start_index(parts: list[str]) -> int | None:
    if not parts:
        return None

    index = 0
    while index < len(parts) and _is_env_assignment(parts[index]):
        index += 1
    if index < len(parts) and os.path.basename(parts[index]) == "env":
        index += 1
        while index < len(parts):
            token = parts[index]
            if token == "--":
                index += 1
                break
            if _is_env_assignment(token) or token in {"-i", "--ignore-environment"}:
                index += 1
                continue
            if token in {"-u", "--unset"} and index + 1 < len(parts):
                index += 2
                continue
            if token.startswith("-u") or token.startswith("--unset="):
                index += 1
                continue
            break
    if index >= len(parts):
        return None
    return index


def command_name(command: str) -> str:
    parts = command_parts(command)
    index = _command_start_index(parts)
    if index is None:
        return ""
    return os.path.basename(parts[index])


def disallowed_command_reason(command: str) -> str | None:
    parts = command_parts(command)
    index = _command_start_index(parts)
    if index is None:
        return None
    name = os.path.basename(parts[index])
    if name in DISALLOWED_COMMANDS:
        return DISALLOWED_COMMANDS[name]
    args = parts[index + 1:]
    if name == "rg" and any(arg == "--pre" or arg.startswith("--pre=") for arg in args):
        return RG_PRE_REASON
    return None


OZM_DIR = os.path.expanduser("~/.ozm")
if os.path.islink(OZM_DIR):
    raise RuntimeError(f"~/.ozm is a symlink — refusing to load config (possible tampering)")
PROJECTS_DIR = os.path.join(OZM_DIR, "projects")
GLOBAL_CONFIG = os.path.join(OZM_DIR, "config.yaml")


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


def _global_config_path() -> str:
    """Path to the user-owned global config."""
    return GLOBAL_CONFIG


def _load_yaml(path: str) -> dict:
    return load_yaml_no_follow(
        path,
        directory=os.path.dirname(path),
        directory_label="config directory",
        file_label="config file",
    )


def _refuse_symlink(path: str, label: str) -> None:
    refuse_symlink(path, label)


def load_project_config() -> dict:
    """Load config from ~/.ozm/projects/. Never reads in-repo files."""
    _refuse_symlink(OZM_DIR, "config directory")
    _refuse_symlink(PROJECTS_DIR, "project config directory")
    path = _project_config_path()
    _refuse_symlink(path, "config file")
    return load_yaml_no_follow(
        path,
        directory=PROJECTS_DIR,
        directory_label="project config directory",
        file_label="config file",
        parent_directory=OZM_DIR,
        parent_label="config directory",
    )


def load_global_config() -> dict:
    """Load config from ~/.ozm/config.yaml."""
    _refuse_symlink(OZM_DIR, "config directory")
    path = _global_config_path()
    _refuse_symlink(path, "config file")
    return load_yaml_no_follow(
        path,
        directory=OZM_DIR,
        directory_label="config directory",
        file_label="config file",
    )


def _save_user_config(config: dict) -> None:
    """Save to the user-owned config file in ~/.ozm/projects/."""
    path = _project_config_path()
    _refuse_symlink(OZM_DIR, "config directory")
    _refuse_symlink(PROJECTS_DIR, "project config directory")
    _refuse_symlink(path, "project config file")
    save_yaml_atomic_no_follow(
        path,
        config,
        directory=PROJECTS_DIR,
        directory_label="project config directory",
        parent_directory=OZM_DIR,
        parent_label="config directory",
        sort_keys=False,
    )


def _save_global_config(config: dict) -> None:
    """Save to the user-owned global config file."""
    path = _global_config_path()
    _refuse_symlink(OZM_DIR, "config directory")
    _refuse_symlink(path, "global config file")
    save_yaml_atomic_no_follow(
        path,
        config,
        directory=OZM_DIR,
        directory_label="config directory",
        sort_keys=False,
    )


def _command_configs() -> list[dict]:
    """Return command configs in local-to-global order."""
    return [load_project_config(), load_global_config()]


def _matching_pattern(command: str, key: str) -> str | None:
    first_word = command.split()[0] if command.strip() else ""
    for config in _command_configs():
        patterns = config.get(key, [])
        if not isinstance(patterns, list):
            continue
        for pattern in patterns:
            if not isinstance(pattern, str):
                continue
            if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(first_word, pattern):
                return pattern
    return None


def is_command_blocked(command: str) -> str | None:
    """Check if a command matches any pattern in blocked_commands."""
    command = sanitize_command(command)
    return _matching_pattern(command, "blocked_commands")


def is_command_allowed(command: str) -> bool:
    """Check if a command matches any allowed_commands pattern."""
    command = sanitize_command(command)
    if disallowed_command_reason(command):
        return False
    if has_shell_metacharacters(command):
        return False
    if is_command_blocked(command):
        return False
    return _matching_pattern(command, "allowed_commands") is not None


def _add_command_pattern(key: str, pattern: str, *, global_scope: bool) -> bool:
    pattern = sanitize_command(pattern).strip()
    if not pattern:
        return False
    config = load_global_config() if global_scope else load_project_config()
    commands = config.get(key, [])
    if not isinstance(commands, list):
        commands = []
    if pattern not in commands:
        commands.append(pattern)
        config[key] = commands
        if global_scope:
            _save_global_config(config)
        else:
            _save_user_config(config)
    return True


def add_allowed_command(pattern: str, *, global_scope: bool = False) -> bool:
    """Append a pattern to allowed_commands in user-owned config."""
    if disallowed_command_reason(pattern):
        return False
    return _add_command_pattern("allowed_commands", pattern, global_scope=global_scope)


def add_blocked_command(pattern: str, *, global_scope: bool = False) -> bool:
    """Append a pattern to blocked_commands in user-owned config."""
    return _add_command_pattern("blocked_commands", pattern, global_scope=global_scope)


def commit_config() -> dict:
    """Return the 'commit' section of the config."""
    config = load_project_config()
    section = config.get("commit", {})
    return section if isinstance(section, dict) else {}


def project_key(key: str) -> str:
    """Prefix a hash key with the project root for project-scoped storage."""
    root = find_project_root()
    return f"{root}\0{key}"

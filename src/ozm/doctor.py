#!/usr/bin/env python3
"""Diagnostics for ozm installation health."""

import hashlib
import json
import os
import shutil

import click

from ozm.install import (
    CODEX_CONFIG,
    CODEX_RULES,
    CODEX_RULES_CONTENT,
    ENFORCE_HOOK,
    HOOK_SCRIPT,
)

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
OZM_MARKER = "ozm — script execution gate"


def _check_ozm_on_path() -> tuple[bool, str]:
    path = shutil.which("ozm")
    if path:
        return True, f"ozm found at {path}"
    return False, "ozm not found on PATH"


def _check_hook_script() -> tuple[bool, str]:
    if not os.path.isfile(ENFORCE_HOOK):
        return False, f"hook script missing: {ENFORCE_HOOK} — run 'ozm install'"
    if not os.access(ENFORCE_HOOK, os.X_OK):
        return False, f"hook exists but not executable: {ENFORCE_HOOK}"
    with open(ENFORCE_HOOK) as f:
        content = f.read()
    expected_hash = hashlib.sha256(HOOK_SCRIPT.encode()).hexdigest()
    actual_hash = hashlib.sha256(content.encode()).hexdigest()
    if actual_hash != expected_hash:
        return False, f"hook content modified — run 'ozm install' to restore"
    return True, f"hook script at {ENFORCE_HOOK}"


def _check_claude_settings() -> tuple[bool, str]:
    if not os.path.isfile(CLAUDE_SETTINGS):
        return False, f"settings.json missing: {CLAUDE_SETTINGS} — run 'ozm install'"
    try:
        with open(CLAUDE_SETTINGS) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return False, f"settings.json unreadable: {e}"

    pre_hooks = settings.get("hooks", {}).get("PreToolUse", [])
    found = any(
        h.get("matcher") == "Bash"
        and any(hk.get("command") == ENFORCE_HOOK for hk in h.get("hooks", []))
        for h in pre_hooks
    )
    if found:
        return True, "Claude Code hook configured in settings.json"
    return False, "ozm hook not found in settings.json — run 'ozm install'"


def _check_codex_project_docs() -> tuple[bool, str]:
    agents_path = os.path.join(os.getcwd(), "AGENTS.md")
    if not os.path.isfile(agents_path):
        return (
            False,
            "AGENTS.md missing in this project — run 'ozm install --project' "
            "before starting Codex here",
        )
    try:
        with open(agents_path) as f:
            content = f.read()
    except OSError as e:
        return False, f"AGENTS.md unreadable: {e}"
    if OZM_MARKER in content:
        return True, "AGENTS.md contains ozm instructions"
    return False, "AGENTS.md exists but does not mention ozm — run 'ozm install --project'"


def _check_codex_enforcement() -> tuple[bool | None, str]:
    if not os.path.isfile(CODEX_CONFIG):
        return False, f"Codex config missing: {CODEX_CONFIG} — run 'ozm install'"
    try:
        with open(CODEX_CONFIG) as f:
            config = f.read()
    except OSError as e:
        return False, f"Codex config unreadable: {e}"

    if "codex_hooks = true" not in config:
        return False, "Codex hooks are not enabled — run 'ozm install'"
    if ENFORCE_HOOK not in config:
        return False, "ozm Codex hook not found in config.toml — run 'ozm install'"
    return True, "Codex hook configured in config.toml"


def _check_codex_rules() -> tuple[bool, str]:
    if not os.path.isfile(CODEX_RULES):
        return False, f"Codex execpolicy rules missing: {CODEX_RULES} — run 'ozm install'"
    try:
        with open(CODEX_RULES) as f:
            content = f.read()
    except OSError as e:
        return False, f"Codex execpolicy rules unreadable: {e}"
    expected_hash = hashlib.sha256(CODEX_RULES_CONTENT.encode()).hexdigest()
    actual_hash = hashlib.sha256(content.encode()).hexdigest()
    if actual_hash != expected_hash:
        return False, "Codex execpolicy rules modified — run 'ozm install' to restore"
    return True, f"Codex execpolicy rules at {CODEX_RULES}"


def _check_pygments() -> tuple[bool, str]:
    try:
        import pygments
        return True, f"pygments {pygments.__version__} available"
    except ImportError:
        return False, "pygments not installed — syntax highlighting disabled"


def _check_project_config() -> tuple[bool, str]:
    from ozm.config import _project_config_path
    path = _project_config_path()
    if os.path.isfile(path):
        return True, f"config at {path}"
    return False, f"no config — run 'ozm trust' to import .ozm.yaml"


@click.command("doctor")
def doctor_cmd() -> None:
    """Check ozm installation health."""
    checks = [
        ("ozm binary", _check_ozm_on_path),
        ("hook script", _check_hook_script),
        ("claude settings", _check_claude_settings),
        ("codex project docs", _check_codex_project_docs),
        ("codex enforcement", _check_codex_enforcement),
        ("codex rules", _check_codex_rules),
        ("pygments", _check_pygments),
        ("project config", _check_project_config),
    ]
    all_ok = True
    for name, check in checks:
        ok, msg = check()
        icon = "INFO" if ok is None else "ok" if ok else "WARN"
        click.echo(f"  [{icon:>4}] {name}: {msg}")
        if ok is False:
            all_ok = False
    if all_ok:
        click.echo("\nAll checks passed.")
    else:
        click.echo("\nSome issues found — see above.")

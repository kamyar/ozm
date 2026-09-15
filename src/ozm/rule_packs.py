#!/usr/bin/env python3
"""Load declarative, user-owned command routing rules.

Rule packs contain local tool names and wrapper forms that do not belong in
Ozm's public source. Packs are enabled explicitly from user-owned config and
loaded only from ~/.ozm/rule-packs/.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ozm.config import OZM_DIR, load_global_config, load_project_config
from ozm.storage import load_yaml_no_follow

RULE_PACKS_DIR = os.path.join(OZM_DIR, "rule-packs")
_PACK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RULE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXECUTABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_PYTHON_MODULE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_MAX_PACKS = 16
_MAX_REDIRECTS_PER_PACK = 32
_MAX_MATCHERS_PER_REDIRECT = 32


@dataclass(frozen=True)
class EntrypointRedirectRule:
    """One data-only rule that replaces wrappers with an installed entry point."""

    source: str
    rule_id: str
    target: str
    python_modules: tuple[str, ...]
    script_basenames: tuple[str, ...]
    guidance: str


def _string_list(value: object, *, field: str, limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeError(f"{field} must be a list")
    if len(value) > limit:
        raise RuntimeError(f"{field} has more than {limit} entries")
    if not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"{field} must contain only strings")
    return tuple(value)


def _enabled_pack_names() -> tuple[str, ...]:
    names: list[str] = []
    for scope, config in (
        ("project config", load_project_config()),
        ("global config", load_global_config()),
    ):
        configured = config.get("rule_packs", [])
        if not isinstance(configured, list):
            raise RuntimeError(f"rule_packs in {scope} must be a list")
        for name in configured:
            if not isinstance(name, str) or not _PACK_NAME.fullmatch(name):
                raise RuntimeError(
                    f"invalid rule pack name in {scope}: names must use only "
                    "letters, numbers, dots, underscores, and hyphens"
                )
            if name not in names:
                names.append(name)
    if len(names) > _MAX_PACKS:
        raise RuntimeError(f"more than {_MAX_PACKS} rule packs are enabled")
    return tuple(names)


def _parse_redirect(pack: str, index: int, value: object) -> EntrypointRedirectRule:
    field = f"rule pack {pack} entrypoint_redirects[{index}]"
    if not isinstance(value, dict):
        raise RuntimeError(f"{field} must be a mapping")
    allowed_keys = {
        "id",
        "target",
        "python_modules",
        "script_basenames",
        "guidance",
    }
    unknown = sorted(set(value) - allowed_keys)
    if unknown:
        raise RuntimeError(f"{field} has unknown fields: {', '.join(unknown)}")

    rule_id = value.get("id")
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise RuntimeError(f"{field}.id is invalid")
    target = value.get("target")
    if not isinstance(target, str) or not _EXECUTABLE.fullmatch(target):
        raise RuntimeError(f"{field}.target must be one executable name")

    modules = _string_list(
        value.get("python_modules"),
        field=f"{field}.python_modules",
        limit=_MAX_MATCHERS_PER_REDIRECT,
    )
    if any(not _PYTHON_MODULE.fullmatch(module) for module in modules):
        raise RuntimeError(f"{field}.python_modules contains an invalid module")

    basenames = _string_list(
        value.get("script_basenames"),
        field=f"{field}.script_basenames",
        limit=_MAX_MATCHERS_PER_REDIRECT,
    )
    if any(
        not basename
        or basename in {".", ".."}
        or os.path.basename(basename) != basename
        or len(basename) > 128
        for basename in basenames
    ):
        raise RuntimeError(f"{field}.script_basenames contains an invalid basename")
    if not modules and not basenames:
        raise RuntimeError(
            f"{field} must define python_modules or script_basenames"
        )

    guidance = value.get(
        "guidance",
        "Use the configured installed entry point directly.",
    )
    if (
        not isinstance(guidance, str)
        or not guidance.strip()
        or "\n" in guidance
        or "\r" in guidance
        or len(guidance) > 240
    ):
        raise RuntimeError(f"{field}.guidance must be one non-empty line")

    return EntrypointRedirectRule(
        source=pack,
        rule_id=rule_id,
        target=target,
        python_modules=modules,
        script_basenames=basenames,
        guidance=guidance.strip(),
    )


def load_entrypoint_redirects() -> tuple[EntrypointRedirectRule, ...]:
    """Load enabled entry-point redirects from private user-owned storage."""
    redirects: list[EntrypointRedirectRule] = []
    seen_ids: set[str] = set()
    for pack in _enabled_pack_names():
        path = os.path.join(RULE_PACKS_DIR, f"{pack}.yaml")
        data = load_yaml_no_follow(
            path,
            directory=RULE_PACKS_DIR,
            directory_label="rule pack directory",
            file_label="rule pack",
            parent_directory=OZM_DIR,
            parent_label="config directory",
        )
        if data.get("version") != 1 or isinstance(data.get("version"), bool):
            raise RuntimeError(f"rule pack {pack} must set version: 1")
        allowed_keys = {"version", "description", "entrypoint_redirects"}
        unknown = sorted(set(data) - allowed_keys)
        if unknown:
            raise RuntimeError(
                f"rule pack {pack} has unknown fields: {', '.join(unknown)}"
            )
        values = data.get("entrypoint_redirects", [])
        if not isinstance(values, list):
            raise RuntimeError(
                f"entrypoint_redirects in rule pack {pack} must be a list"
            )
        if len(values) > _MAX_REDIRECTS_PER_PACK:
            raise RuntimeError(
                f"rule pack {pack} has more than "
                f"{_MAX_REDIRECTS_PER_PACK} entry-point redirects"
            )
        for index, value in enumerate(values):
            redirect = _parse_redirect(pack, index, value)
            qualified_id = f"{pack}/{redirect.rule_id}"
            if qualified_id in seen_ids:
                raise RuntimeError(f"duplicate rule ID: {qualified_id}")
            seen_ids.add(qualified_id)
            redirects.append(redirect)
    return tuple(redirects)

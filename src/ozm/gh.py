#!/usr/bin/env python3
"""Operation-aware GitHub CLI proxy."""

import click

from ozm.cmd import _cmd_impl


@click.command(
    "gh",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "allow_interspersed_args": False,
    },
)
@click.argument("items", nargs=-1, type=click.UNPROCESSED, required=True)
def gh_cmd(items: tuple[str, ...]) -> None:
    """Run GitHub CLI operations through ozm policy.

    Proven read-only high-level commands, REST GET/HEAD requests, and GraphQL
    queries run directly. Use pr review-reply for typed review replies; the
    equivalent raw REST POST is rejected. Writes and unknown operations use the
    normal command approval, blocklist, allowlist, and cache flow. The real gh
    executable is resolved from trusted system locations before execution.
    """
    _cmd_impl(False, ("gh", *items), github_proxy=True)

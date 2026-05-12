#!/usr/bin/env python3
"""Append-only audit log for ozm approvals and denials."""

import os
import json
from datetime import datetime, timezone

import click

OZM_DIR = os.path.expanduser("~/.ozm")
AUDIT_FILE = os.path.join(OZM_DIR, "audit.log")


def _one_line(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)[1:-1]


def log(action: str, kind: str, target: str, feedback: str | None = None) -> None:
    """Append an entry to the audit log.

    action: "clicked", "cached", "config", "denied", "blocked", "no-dialog"
    kind: "run", "cmd"
    target: the script path or command string
    """
    os.makedirs(OZM_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cwd = _one_line(os.getcwd())
    line = f"{ts}  {_one_line(action):<9}  {_one_line(kind):<3}  {cwd}  {_one_line(target)}"
    if feedback:
        line += f"  # {_one_line(feedback)}"
    with open(AUDIT_FILE, "a") as f:
        f.write(line + "\n")


@click.command("log")
@click.option("-n", "count", default=20, type=click.IntRange(min=1), help="Number of entries to show.")
def log_cmd(count: int) -> None:
    """Show recent audit log entries."""
    if not os.path.exists(AUDIT_FILE):
        click.echo("No audit log yet.")
        return
    with open(AUDIT_FILE) as f:
        lines = f.readlines()
    for line in lines[-count:]:
        click.echo(line.rstrip())

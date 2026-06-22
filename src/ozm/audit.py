#!/usr/bin/env python3
"""Append-only audit log for ozm approvals and denials."""

import os
import json
import re
from datetime import datetime, timezone

import click

OZM_DIR = os.path.expanduser("~/.ozm")
AUDIT_FILE = os.path.join(OZM_DIR, "audit.log")


def _one_line(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)[1:-1]


def log(action: str, kind: str, target: str, feedback: str | None = None) -> None:
    """Append an entry to the audit log.

    action: "clicked", "cached", "config", "semantic", "denied", "blocked", "no-dialog"
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


_LOG_RE = re.compile(r"^(?P<timestamp>.{19})  (?P<action>.{1,9})  (?P<kind>.{1,3})  (?P<rest>.*)$")


def _decode_one_line(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def parse_line(line: str) -> dict:
    raw = line.rstrip("\n")
    match = _LOG_RE.match(raw)
    if not match:
        return {"raw": raw}
    rest = match.group("rest")
    feedback = None
    if "  # " in rest:
        rest, feedback = rest.split("  # ", 1)
    cwd = ""
    target = rest
    if "  " in rest:
        cwd, target = rest.split("  ", 1)
    entry = {
        "timestamp": match.group("timestamp"),
        "action": _decode_one_line(match.group("action").strip()),
        "kind": _decode_one_line(match.group("kind").strip()),
        "cwd": _decode_one_line(cwd),
        "target": _decode_one_line(target),
        "raw": raw,
    }
    if feedback is not None:
        entry["feedback"] = _decode_one_line(feedback)
    return entry


@click.command("log")
@click.option("-n", "count", default=20, type=click.IntRange(min=1), help="Number of entries to show.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def log_cmd(count: int, json_output: bool) -> None:
    """Show recent audit log entries."""
    if not os.path.exists(AUDIT_FILE):
        if json_output:
            click.echo(json.dumps({"entries": []}, sort_keys=True))
        else:
            click.echo("No audit log yet.")
        return
    with open(AUDIT_FILE) as f:
        lines = f.readlines()
    selected = lines[-count:]
    if json_output:
        click.echo(json.dumps({"entries": [parse_line(line) for line in selected]}, sort_keys=True))
        return
    for line in selected:
        click.echo(line.rstrip())

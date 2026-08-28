#!/usr/bin/env python3
"""Append-only audit log for ozm approvals and denials."""

import os
import json
import re
import stat
from collections import Counter
from datetime import datetime, timedelta, timezone

import click

from ozm.storage import ensure_private_dir

OZM_DIR = os.path.expanduser("~/.ozm")
AUDIT_FILE = os.path.join(OZM_DIR, "audit.log")


def _open_audit_file() -> int:
    """Open the audit log append-only, user-private, without following symlinks."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    fd = os.open(AUDIT_FILE, flags, 0o600)
    try:
        st = os.fstat(fd)
        if stat.S_IMODE(st.st_mode) & 0o077:
            os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    return fd


def _one_line(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)[1:-1]


def log(action: str, kind: str, target: str, feedback: str | None = None) -> None:
    """Append an entry to the audit log.

    action: "clicked", "cached", "config", "semantic", "denied", "blocked", "no-dialog"
    kind: "run", "cmd"
    target: the script path or command string
    """
    ensure_private_dir(OZM_DIR, "audit directory")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cwd = _one_line(os.getcwd())
    line = f"{ts}  {_one_line(action):<9}  {_one_line(kind):<3}  {cwd}  {_one_line(target)}"
    if feedback:
        line += f"  # {_one_line(feedback)}"
    with os.fdopen(_open_audit_file(), "a") as f:
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


_DURATION = re.compile(r"^([1-9][0-9]*)([smhd])$")


def _duration(value: str) -> timedelta:
    match = _DURATION.fullmatch(value.strip())
    if not match:
        raise click.BadParameter("use a duration such as 30m, 3h, or 2d")
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return timedelta(seconds=seconds)


def _entry_timestamp(entry: dict) -> datetime | None:
    try:
        return datetime.strptime(
            entry["timestamp"],
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _summary(entries: list[dict]) -> dict:
    actions = Counter(entry.get("action", "unknown") for entry in entries)
    kinds = Counter(entry.get("kind", "unknown") for entry in entries)
    action_kinds = Counter(
        f"{entry.get('action', 'unknown')}:{entry.get('kind', 'unknown')}"
        for entry in entries
    )
    manual_approvals = actions["clicked"] + actions["override"]
    manual_denials = actions["denied"]
    generated_run_approvals = sum(
        1
        for entry in entries
        if entry.get("action") == "clicked"
        and entry.get("kind") == "run"
        and str(entry.get("target", "")).startswith(("shell:", "stdin:"))
    )
    return {
        "entries": len(entries),
        "manual_approvals": manual_approvals,
        "manual_denials": manual_denials,
        "generated_run_approvals": generated_run_approvals,
        "actions": dict(sorted(actions.items())),
        "kinds": dict(sorted(kinds.items())),
        "action_kinds": dict(sorted(action_kinds.items())),
    }


@click.command("log")
@click.option("-n", "count", default=None, type=click.IntRange(min=1), help="Number of entries to show.")
@click.option("--since", metavar="DURATION", help="Select entries from a duration such as 3h or 2d.")
@click.option("--summary", "summary_output", is_flag=True, help="Summarize selected audit decisions.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def log_cmd(
    count: int | None,
    since: str | None,
    summary_output: bool,
    json_output: bool,
) -> None:
    """Show or summarize recent audit log entries."""
    if not os.path.exists(AUDIT_FILE):
        if summary_output:
            summary = _summary([])
            if json_output:
                click.echo(json.dumps({"summary": summary}, sort_keys=True))
            else:
                click.echo("entries: 0")
                click.echo("manual approvals: 0")
                click.echo("manual denials: 0")
                click.echo("generated run approvals: 0")
            return
        if json_output:
            click.echo(json.dumps({"entries": []}, sort_keys=True))
        else:
            click.echo("No audit log yet.")
        return
    with open(AUDIT_FILE) as f:
        lines = f.readlines()
    entries = [parse_line(line) for line in lines]
    if since is not None:
        cutoff = datetime.now(timezone.utc) - _duration(since)
        entries = [
            entry
            for entry in entries
            if (timestamp := _entry_timestamp(entry)) is not None
            and timestamp >= cutoff
        ]
    effective_count = count if count is not None else None if summary_output else 20
    if effective_count is not None:
        entries = entries[-effective_count:]
    if summary_output:
        summary = _summary(entries)
        if json_output:
            click.echo(json.dumps({"summary": summary}, sort_keys=True))
            return
        click.echo(f"entries: {summary['entries']}")
        click.echo(f"manual approvals: {summary['manual_approvals']}")
        click.echo(f"manual denials: {summary['manual_denials']}")
        click.echo(
            f"generated run approvals: {summary['generated_run_approvals']}"
        )
        click.echo("actions:")
        for action, action_count in summary["actions"].items():
            click.echo(f"  {action}: {action_count}")
        click.echo("kinds:")
        for kind, kind_count in summary["kinds"].items():
            click.echo(f"  {kind}: {kind_count}")
        return
    if json_output:
        click.echo(json.dumps({"entries": entries}, sort_keys=True))
        return
    for entry in entries:
        click.echo(entry["raw"])

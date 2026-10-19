#!/usr/bin/env python3
"""Append-only audit decisions with compatible text and structured context."""

import json
import os
import re
import shlex
import stat
from collections import Counter
from datetime import datetime, timedelta, timezone

import click

from ozm.storage import ensure_private_dir

OZM_DIR = os.path.expanduser("~/.ozm")
AUDIT_FILE = os.path.join(OZM_DIR, "audit.log")
_CONTEXT_KEY = "ozm_audit"
_DETAIL_MARKER = "\t@ozm:"
_CONTEXT_FIELDS = {
    "agent", "rule", "source", "script_sha256", "executable_lines",
    "families", "nested_families", "classification", "failure_category",
}
_REASONS = {
    "clicked": "manual approval", "override": "one-time override",
    "cached": "approval cache", "config": "command permission",
    "semantic": "proven read", "operation": "scoped operation permission",
    "denied": "manual denial", "blocked": "policy block",
    "invalid": "validation failure", "error": "gate error",
    "no-dialog": "approval dialog unavailable",
}


def begin_context() -> None:
    """Start fresh audit context for one Click command invocation."""
    context = click.get_current_context(silent=True)
    if context is not None:
        context.meta[_CONTEXT_KEY] = {}


def set_context(**fields) -> None:
    """Set safe diagnostic fields. These fields never authorize execution."""
    context = click.get_current_context(silent=True)
    if context is not None:
        details = context.meta.setdefault(_CONTEXT_KEY, {})
        details.update({key: value for key, value in fields.items() if key in _CONTEXT_FIELDS})


def _open_audit_file() -> int:
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    fd = os.open(AUDIT_FILE, flags, 0o600)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
            os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    return fd


def _one_line(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)[1:-1]


def _command_family(target: str) -> str:
    try:
        parts = shlex.split(target)
    except ValueError:
        return "unknown"
    if not parts:
        return "unknown"
    family = os.path.basename(parts[0])
    return family if re.fullmatch(r"[A-Za-z0-9_.+-]+", family) else "unknown"


def log(action: str, kind: str, target: str, feedback: str | None = None) -> None:
    """Append one decision. New fields do not copy descriptions or arguments.

    The existing target and feedback fields retain their original behavior.
    A tab-delimited JSON suffix adds safe, versioned context to new records.
    """
    ensure_private_dir(OZM_DIR, "audit directory")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    context = click.get_current_context(silent=True)
    details = dict(context.meta.get(_CONTEXT_KEY, {})) if context is not None else {}
    details["version"] = 1
    details["decision_reason"] = _REASONS.get(action, "unknown decision")
    details["command_family"] = (
        "generated shell" if kind == "run" and target.startswith("shell:")
        else "generated stdin" if kind == "run" and target.startswith("stdin:")
        else "script" if kind == "run"
        else "unknown" if action == "invalid"
        else _command_family(target)
    )
    line = f"{ts}  {_one_line(action):<9}  {_one_line(kind):<3}  {_one_line(os.getcwd())}  {_one_line(target)}"
    if feedback:
        line += f"  # {_one_line(feedback)}"
    line += _DETAIL_MARKER + json.dumps(details, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    with os.fdopen(_open_audit_file(), "a") as file:
        file.write(line + "\n")


_LOG_RE = re.compile(r"^(?P<timestamp>.{19})  (?P<action>[^ ]+) +(?P<kind>[^ ]+) +(?P<rest>.*)$")


def _decode_one_line(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def parse_line(line: str) -> dict:
    raw = line.rstrip("\n")
    text, marker, suffix = raw.rpartition(_DETAIL_MARKER)
    details = {}
    if marker:
        try:
            candidate = json.loads(suffix)
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict) and candidate.get("version") == 1:
            details = {
                key: value for key, value in candidate.items()
                if key in _CONTEXT_FIELDS | {"command_family", "decision_reason"}
            }
        else:
            text = raw
    else:
        text = raw
    match = _LOG_RE.match(text)
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
        "action": _decode_one_line(match.group("action")),
        "kind": _decode_one_line(match.group("kind")),
        "cwd": _decode_one_line(cwd),
        "target": _decode_one_line(target),
        "raw": raw,
        **details,
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
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    try:
        return timedelta(seconds=seconds)
    except OverflowError as exc:
        raise click.BadParameter("duration is too large") from exc


def _entry_timestamp(entry: dict) -> datetime | None:
    try:
        return datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _generated_families(entry: dict) -> list[str]:
    families = entry.get("families")
    if not isinstance(families, list):
        match = re.search(r"(?:^|; )families=([^; ]+)", str(entry.get("feedback", "")))
        families = match.group(1).split(",") if match else ["unknown"]
    return list(dict.fromkeys(
        family for family in families
        if isinstance(family, str) and re.fullmatch(r"[A-Za-z0-9_.+-]+", family)
    ))[:8] or ["unknown"]


def _approval_type(entry: dict) -> str:
    if entry.get("kind") == "run":
        target = str(entry.get("target", ""))
        if target.startswith("shell:"):
            return "generated shell"
        if target.startswith("stdin:"):
            return "generated stdin"
        return "script"
    return "git" if entry.get("kind") == "git" else "direct command"


def _summary(entries: list[dict]) -> dict:
    actions = Counter(entry.get("action", "unknown") for entry in entries)
    kinds = Counter(entry.get("kind", "unknown") for entry in entries)
    action_kinds = Counter(f"{entry.get('action', 'unknown')}:{entry.get('kind', 'unknown')}" for entry in entries)
    approvals = [entry for entry in entries if entry.get("action") in {"clicked", "override"}]
    by_family = Counter()
    by_type = Counter()
    generated = Counter()
    for entry in approvals:
        approval_type = _approval_type(entry)
        by_type[approval_type] += 1
        family = entry.get("command_family")
        if not isinstance(family, str):
            family = approval_type if entry.get("kind") == "run" else _command_family(str(entry.get("target", "")))
        by_family[family] += 1
        if approval_type in {"generated shell", "generated stdin"}:
            generated.update(_generated_families(entry))
    return {
        "entries": len(entries),
        "manual_approvals": len(approvals),
        "manual_denials": actions["denied"],
        "blocked_commands": actions["blocked"],
        "validation_failures": actions["invalid"],
        "generated_run_approvals": sum(1 for entry in approvals if entry.get("action") == "clicked" and _approval_type(entry) in {"generated shell", "generated stdin"}),
        "manual_approvals_by_family": dict(sorted(by_family.items())),
        "manual_approvals_by_type": dict(sorted(by_type.items())),
        "generated_approvals_by_family": dict(sorted(generated.items())),
        "actions": dict(sorted(actions.items())),
        "kinds": dict(sorted(kinds.items())),
        "action_kinds": dict(sorted(action_kinds.items())),
    }


def _selection_text(selection: dict) -> str:
    window = f"since {selection['window_start']} UTC" if selection["window_start"] else "all recorded dates"
    limit = f"last {selection['limit']} entries" if selection["limit"] is not None else "no entry limit"
    return f"selection: {window}; through {selection['window_end']} UTC; {limit}; {selection['returned']} of {selection['matched']} entries"


@click.command("log")
@click.option("-n", "count", default=None, type=click.IntRange(min=1), help="Number of entries to show.")
@click.option("--since", metavar="DURATION", help="Select entries from a duration such as 3h or 2d.")
@click.option("--summary", "summary_output", is_flag=True, help="Summarize selected audit decisions.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def log_cmd(count: int | None, since: str | None, summary_output: bool, json_output: bool) -> None:
    """Show or summarize recent audit decisions and the selection limits."""
    now = datetime.now(timezone.utc)
    try:
        cutoff = now - _duration(since) if since is not None else None
    except OverflowError as exc:
        raise click.BadParameter("duration is too large") from exc
    entries = []
    if os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE) as file:
            entries = [parse_line(line) for line in file]
    if cutoff is not None:
        entries = [entry for entry in entries if (timestamp := _entry_timestamp(entry)) is not None and cutoff <= timestamp <= now]
    matched = len(entries)
    effective_count = count if count is not None else None if summary_output else 20
    if effective_count is not None:
        entries = entries[-effective_count:]
    selection = {
        "since": since,
        "window_start": cutoff.strftime("%Y-%m-%d %H:%M:%S") if cutoff else None,
        "window_end": now.strftime("%Y-%m-%d %H:%M:%S"),
        "limit": effective_count,
        "matched": matched,
        "returned": len(entries),
    }
    if summary_output:
        summary = _summary(entries)
        if json_output:
            click.echo(json.dumps({"summary": summary, "selection": selection}, sort_keys=True))
            return
        click.echo(_selection_text(selection))
        for key, label in (
            ("entries", "entries"), ("manual_approvals", "manual approvals"),
            ("manual_denials", "manual denials"), ("blocked_commands", "blocked commands"),
            ("validation_failures", "validation failures"),
            ("generated_run_approvals", "generated run approvals"),
        ):
            click.echo(f"{label}: {summary[key]}")
        for key, label in (
            ("manual_approvals_by_family", "manual approvals by command family"),
            ("manual_approvals_by_type", "manual approvals by type"),
            ("generated_approvals_by_family", "generated command families (one decision can include several)"),
            ("actions", "actions"), ("kinds", "kinds"),
        ):
            click.echo(f"{label}:")
            for name, amount in summary[key].items():
                click.echo(f"  {name}: {amount}")
        return
    if json_output:
        click.echo(json.dumps({"entries": entries, "selection": selection}, sort_keys=True))
        return
    click.echo(_selection_text(selection), err=True)
    if not entries:
        click.echo("No audit entries in this selection.")
    for entry in entries:
        click.echo(entry["raw"])

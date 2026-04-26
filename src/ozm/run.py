#!/usr/bin/env python3
"""Hash-based script execution gate."""

import hashlib
import os
import stat
import subprocess
import sys

import click
import yaml

from ozm.approve import request_approval
from ozm.audit import log as audit_log
from ozm.config import project_key

OZM_DIR = os.path.expanduser("~/.ozm")
HASH_FILE = os.path.join(OZM_DIR, "hashes.yaml")


def compute_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def resolve_path(path: str) -> str:
    return os.path.abspath(path)


def load_hashes() -> dict[str, str]:
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE) as f:
            data = yaml.safe_load(f)
            return data if data else {}
    return {}


def save_hashes(hashes: dict[str, str]) -> None:
    os.makedirs(OZM_DIR, exist_ok=True)
    with open(HASH_FILE, "w") as f:
        yaml.dump(hashes, f, default_flow_style=False, sort_keys=True)


def show_file(path: str) -> None:
    with open(path) as f:
        content = f.read()
    lines = content.splitlines()
    width = len(str(len(lines)))
    click.echo(f"\n{'=' * 60}")
    click.echo(f"  {path}")
    click.echo(f"{'=' * 60}")
    for i, line in enumerate(lines, 1):
        click.echo(f"  {i:>{width}} | {line}")
    click.echo(f"{'=' * 60}\n")


def ensure_executable(path: str) -> None:
    st = os.stat(path)
    if not st.st_mode & stat.S_IXUSR:
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@click.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("script")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run_cmd(script: str, args: tuple[str, ...]) -> None:
    """Run a script after content review (hash-gated)."""
    if not os.path.exists(script):
        raise click.ClickException(f"{script}: not found")

    abs_path = resolve_path(script)
    key = project_key(abs_path)
    current_hash = compute_hash(script)
    hashes = load_hashes()
    stored_hash = hashes.get(key)

    if stored_hash == current_hash:
        audit_log("allowed", "run", abs_path)
        ensure_executable(script)
        result = subprocess.run([script, *args])
        sys.exit(result.returncode)

    label = "NEW" if stored_hash is None else "CHANGED"

    approval = request_approval(script, label)

    if approval.approved is True:
        hashes[key] = current_hash
        save_hashes(hashes)
        audit_log("allowed", "run", abs_path, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: approved {script} — {approval.feedback}", err=True)
        else:
            click.echo(f"ozm: approved {script}")
        ensure_executable(script)
        result = subprocess.run([script, *args])
        sys.exit(result.returncode)

    if approval.approved is False:
        audit_log("denied", "run", abs_path, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: denied {script} — {approval.feedback}", err=True)
        else:
            click.echo(f"ozm: denied {script}", err=True)
        sys.exit(1)

    click.echo(f"ozm: [{label}] {script}")
    show_file(script)

    hashes[key] = current_hash
    save_hashes(hashes)

    click.echo("Review the content above. Run the same command again to execute.")
    sys.exit(1)


@click.command("status")
def status_cmd() -> None:
    """Show tracked files and commands with their approval status."""
    from ozm.config import find_project_root

    root = find_project_root()
    prefix = root + ":"
    hashes = load_hashes()
    entries = {k: v for k, v in hashes.items() if k.startswith(prefix)}
    if not entries:
        click.echo("No tracked entries.")
        return
    for key, stored_hash in sorted(entries.items()):
        display = key[len(prefix):]
        if "cmd:" in display:
            label = "ok"
        elif os.path.exists(display):
            current = compute_hash(display)
            label = "ok" if current == stored_hash else "CHANGED"
        else:
            label = "MISSING"
        click.echo(f"  [{label:>7}] {display}")


@click.command("reset")
@click.argument("script", required=False)
@click.option("--all", "reset_all", is_flag=True, help="Forget all approvals.")
def reset_cmd(script: str | None, reset_all: bool) -> None:
    """Forget approval for a script (or all scripts with --all)."""
    from ozm.config import find_project_root

    root = find_project_root()
    prefix = root + ":"

    if reset_all:
        hashes = load_hashes()
        hashes = {k: v for k, v in hashes.items() if not k.startswith(prefix)}
        save_hashes(hashes)
        click.echo("All approvals cleared for this project.")
        return

    if not script:
        raise click.ClickException("Provide a script name, or use --all.")

    abs_path = resolve_path(script)
    key = project_key(abs_path)
    hashes = load_hashes()
    if key not in hashes:
        raise click.ClickException(f"{script} is not tracked.")
    del hashes[key]
    save_hashes(hashes)
    click.echo(f"Forgot approval for {script}")

#!/usr/bin/env python3
"""Hash-based script execution gate.

First run with new/changed content displays the file and exits.
Second run with the same content executes it.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys

import click

from ozm.approve import request_approval

HASH_FILE = ".ozm-hashes.json"


def compute_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_hashes() -> dict[str, str]:
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE) as f:
            return json.load(f)
    return {}


def save_hashes(hashes: dict[str, str]) -> None:
    with open(HASH_FILE, "w") as f:
        json.dump(hashes, f, indent=2, sort_keys=True)
        f.write("\n")


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
    """Run a script after content review.

    First invocation with new or changed content shows the file and exits.
    Run the same command again to execute.
    """
    if not os.path.exists(script):
        raise click.ClickException(f"{script}: not found")

    current_hash = compute_hash(script)
    hashes = load_hashes()
    stored_hash = hashes.get(script)

    if stored_hash == current_hash:
        ensure_executable(script)
        result = subprocess.run([script, *args])
        sys.exit(result.returncode)

    label = "NEW" if stored_hash is None else "CHANGED"

    approved = request_approval(script, label)

    if approved is True:
        hashes[script] = current_hash
        save_hashes(hashes)
        click.echo(f"ozm: approved {script}")
        ensure_executable(script)
        result = subprocess.run([script, *args])
        sys.exit(result.returncode)

    if approved is False:
        click.echo(f"ozm: denied {script}")
        sys.exit(1)

    # Fallback: no dialog available, use show-and-retry flow
    click.echo(f"ozm: [{label}] {script}")
    show_file(script)

    hashes[script] = current_hash
    save_hashes(hashes)

    click.echo("Review the content above. Run the same command again to execute.")
    sys.exit(1)


@click.command("status")
def status_cmd() -> None:
    """Show tracked files and their approval status."""
    hashes = load_hashes()
    if not hashes:
        click.echo("No tracked files.")
        return
    for path, stored_hash in sorted(hashes.items()):
        if os.path.exists(path):
            current = compute_hash(path)
            label = "ok" if current == stored_hash else "CHANGED"
        else:
            label = "MISSING"
        click.echo(f"  [{label:>7}] {path}")


@click.command("reset")
@click.argument("script", required=False)
@click.option("--all", "reset_all", is_flag=True, help="Forget all approvals.")
def reset_cmd(script: str | None, reset_all: bool) -> None:
    """Forget approval for a script (or all scripts with --all)."""
    if reset_all:
        if os.path.exists(HASH_FILE):
            os.remove(HASH_FILE)
        click.echo("All approvals cleared.")
        return

    if not script:
        raise click.ClickException("Provide a script name, or use --all.")

    hashes = load_hashes()
    if script not in hashes:
        raise click.ClickException(f"{script} is not tracked.")
    del hashes[script]
    save_hashes(hashes)
    click.echo(f"Forgot approval for {script}")

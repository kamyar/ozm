#!/usr/bin/env python3
"""Hash-based script execution gate."""

import hashlib
import os
import stat
import subprocess
import sys

import click
from ozm.agent import extract_agent_metadata
from ozm.approve import request_approval
from ozm.audit import log as audit_log
from ozm.config import project_key
from ozm.storage import load_yaml_no_follow, refuse_symlink, save_yaml_atomic_no_follow

OZM_DIR = os.path.expanduser("~/.ozm")
HASH_FILE = os.path.join(OZM_DIR, "hashes.yaml")


def _refuse_symlink(path: str, label: str) -> None:
    refuse_symlink(path, label)


def compute_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def resolve_path(path: str) -> str:
    return os.path.abspath(path)


def load_hashes() -> dict[str, str]:
    _refuse_symlink(OZM_DIR, "approval cache directory")
    _refuse_symlink(HASH_FILE, "approval cache file")
    return load_yaml_no_follow(
        HASH_FILE,
        directory=OZM_DIR,
        directory_label="approval cache directory",
        file_label="approval cache file",
    )


def save_hashes(hashes: dict[str, str]) -> None:
    _refuse_symlink(OZM_DIR, "approval cache directory")
    _refuse_symlink(HASH_FILE, "approval cache file")
    save_yaml_atomic_no_follow(
        HASH_FILE,
        hashes,
        directory=OZM_DIR,
        directory_label="approval cache directory",
        sort_keys=True,
    )


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


def _display_key_target(root: str, target: str) -> str:
    if not os.path.isabs(target):
        return target
    try:
        if os.path.commonpath([root, target]) == root:
            return os.path.relpath(target, root)
    except ValueError:
        pass
    return target


@click.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("items", nargs=-1, type=click.UNPROCESSED, required=True)
def run_cmd(items: tuple[str, ...]) -> None:
    """Run a script after content review (hash-gated)."""
    parts, agent = extract_agent_metadata(list(items))
    if not parts:
        raise click.ClickException("Provide a script to run.")

    script = parts[0]
    args = tuple(parts[1:])

    if not os.path.exists(script):
        raise click.ClickException(f"{script}: not found")
    if not os.path.isfile(script):
        raise click.ClickException(f"{script}: not a file")

    abs_path = resolve_path(script)
    key = project_key(abs_path)
    current_hash = compute_hash(script)
    try:
        hashes = load_hashes()
    except (OSError, RuntimeError) as exc:
        audit_log("error", "run", abs_path, str(exc))
        raise click.ClickException(
            f"approval cache error: {exc}. The script was NOT executed."
        ) from exc
    stored_hash = hashes.get(key)

    if stored_hash == current_hash:
        audit_log("cached", "run", abs_path)
        click.echo("ozm: allowed (cached)", err=True)
        ensure_executable(script)
        result = subprocess.run([abs_path, *args])
        sys.exit(result.returncode)

    label = "NEW" if stored_hash is None else "CHANGED"

    approval = request_approval(script, label, agent)

    if approval.approved is True:
        hashes[key] = current_hash
        try:
            save_hashes(hashes)
        except (OSError, RuntimeError) as exc:
            audit_log("error", "run", abs_path, str(exc))
            raise click.ClickException(
                f"could not save approval cache: {exc}. The script was NOT executed."
            ) from exc
        audit_log("clicked", "run", abs_path, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: approved {script} — {approval.feedback}", err=True)
        else:
            click.echo(f"ozm: approved {script}")
        ensure_executable(script)
        result = subprocess.run([abs_path, *args])
        sys.exit(result.returncode)

    if approval.approved is False:
        audit_log("denied", "run", abs_path, approval.feedback)
        if approval.feedback:
            click.echo(f"ozm: denied {script} — {approval.feedback}", err=True)
        else:
            click.echo(f"ozm: denied {script}", err=True)
        sys.exit(1)

    audit_log("no-dialog", "run", abs_path, approval.feedback)
    click.echo(f"ozm: [{label}] {script}")
    show_file(script)
    if approval.feedback:
        click.echo(f"ozm: dialog error: {approval.feedback}", err=True)
    click.echo(
        "ozm: BLOCKED — approval dialog could not be displayed. "
        "The script was NOT executed. "
        "Do NOT retry. "
        "Tell the user ozm needs a macOS GUI session to approve this script.",
        err=True,
    )
    sys.exit(1)


@click.command("status")
def status_cmd() -> None:
    """Show tracked files and commands with their approval status."""
    from ozm.config import find_project_root

    root = find_project_root()
    prefix = root + "\0"
    hashes = load_hashes()
    entries = {k: v for k, v in hashes.items() if k.startswith(prefix)}
    if not entries:
        click.echo("No tracked entries.")
        return
    for key, stored_hash in sorted(entries.items()):
        target = key[len(prefix):]
        display = _display_key_target(root, target)
        if target.startswith("cmd:"):
            label = "ok"
        elif os.path.exists(target):
            current = compute_hash(target)
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
    prefix = root + "\0"

    if reset_all:
        if script:
            raise click.ClickException("Use either a script name or --all, not both.")
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

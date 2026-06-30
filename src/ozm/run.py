#!/usr/bin/env python3
"""Hash-based script execution gate."""

import difflib
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile

import click
from ozm.agent import extract_agent_metadata
from ozm.approve import request_approval
from ozm.audit import log as audit_log
from ozm.exit_codes import CONFIG_ERROR, DENIED, NO_DIALOG, click_error
from ozm.config import project_key
from ozm.storage import (
    load_yaml_no_follow,
    refuse_symlink,
    save_bytes_atomic_no_follow,
    save_yaml_atomic_no_follow,
)

OZM_DIR = os.path.expanduser("~/.ozm")
HASH_FILE = os.path.join(OZM_DIR, "hashes.yaml")
SNAPSHOTS_DIR = os.path.join(OZM_DIR, "snapshots")
STDIN_PREFIX = "stdin:"
SHELL_PREFIX = "shell:"


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


def _snapshot_path(key: str) -> str:
    slug = hashlib.sha256(key.encode()).hexdigest()
    return os.path.join(SNAPSHOTS_DIR, slug)


def save_snapshot(key: str, file_path: str) -> None:
    _refuse_symlink(OZM_DIR, "snapshot directory")
    _refuse_symlink(SNAPSHOTS_DIR, "snapshot directory")
    dest = _snapshot_path(key)
    _refuse_symlink(dest, "snapshot file")
    with open(file_path, "rb") as f:
        content = f.read()
    save_bytes_atomic_no_follow(
        dest,
        content,
        directory=SNAPSHOTS_DIR,
        directory_label="snapshot directory",
        parent_directory=OZM_DIR,
        parent_label="snapshot directory",
    )


def load_snapshot(key: str) -> str | None:
    path = _snapshot_path(key)
    if not os.path.exists(path) or os.path.islink(path):
        return None
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def snapshot_diff(key: str, file_path: str) -> tuple[str | None, int, int]:
    old_content = load_snapshot(key)
    if old_content is None:
        return None, 0, 0
    try:
        with open(file_path) as f:
            new_content = f.read()
    except OSError:
        return None, 0, 0
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{os.path.basename(file_path)}",
        tofile=f"b/{os.path.basename(file_path)}",
    ))
    if not diff_lines:
        return None, 0, 0
    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    return "".join(diff_lines), added, removed


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


def compute_content_hash(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode()
    return hashlib.sha256(content).hexdigest()


def _safe_title_suffix(title: str) -> str:
    base = os.path.basename(title.strip()) or "stdin-script"
    _root, ext = os.path.splitext(base)
    if ext and all(ch.isalnum() or ch in ".-_" for ch in ext):
        return ext
    return ".sh"


def _write_temp_script(content: str, title: str) -> str:
    fd, path = tempfile.mkstemp(prefix="ozm-stdin-", suffix=_safe_title_suffix(title))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        ensure_executable(path)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _cleanup(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _execute_script(abs_path: str, args: tuple[str, ...]) -> None:
    ensure_executable(abs_path)
    result = subprocess.run([abs_path, *args])
    sys.exit(result.returncode)


def _display_key_target(root: str, target: str) -> str:
    if not os.path.isabs(target):
        return target
    try:
        if os.path.commonpath([root, target]) == root:
            return os.path.relpath(target, root)
    except ValueError:
        pass
    return target


def _run_reviewed_script(
    script: str,
    args: tuple[str, ...],
    agent,
    *,
    key_target: str | None = None,
    display_name: str | None = None,
    current_hash: str | None = None,
    cleanup_path: str | None = None,
) -> None:
    abs_path = resolve_path(script)
    key_target = key_target or abs_path
    display_name = display_name or script
    dialog_display_path = display_name if key_target.startswith((STDIN_PREFIX, SHELL_PREFIX)) else None
    audit_target = key_target
    key = project_key(key_target)
    if current_hash is None:
        current_hash = compute_hash(script)
    try:
        hashes = load_hashes()
    except (OSError, RuntimeError) as exc:
        audit_log("error", "run", audit_target, str(exc))
        _cleanup(cleanup_path)
        raise click_error(
            f"approval cache error: {exc}. The script was NOT executed.",
            CONFIG_ERROR,
        ) from exc
    stored_hash = hashes.get(key)

    try:
        if stored_hash == current_hash:
            audit_log("cached", "run", audit_target)
            click.echo("ozm: allowed (cached)", err=True)
            _execute_script(abs_path, args)

        label = "NEW" if stored_hash is None else "CHANGED"

        snap_diff = None
        if label == "CHANGED":
            snap_diff, _, _ = snapshot_diff(key, abs_path)

        approval = request_approval(
            script,
            label,
            agent,
            snapshot_diff=snap_diff,
            display_path=dialog_display_path,
        )

        if approval.approved is True:
            hashes[key] = current_hash
            try:
                save_hashes(hashes)
            except (OSError, RuntimeError) as exc:
                audit_log("error", "run", audit_target, str(exc))
                raise click_error(
                    f"could not save approval cache: {exc}. The script was NOT executed.",
                    CONFIG_ERROR,
                ) from exc
            try:
                save_snapshot(key, abs_path)
            except (OSError, RuntimeError):
                pass
            audit_log("clicked", "run", audit_target, approval.feedback)
            if approval.feedback:
                click.echo(f"ozm: approved {display_name} — [user] {approval.feedback}", err=True)
            else:
                click.echo(f"ozm: approved {display_name}")
            _execute_script(abs_path, args)

        if approval.approved is False:
            audit_log("denied", "run", audit_target, approval.feedback)
            if approval.feedback:
                click.echo(f"ozm: denied {display_name} — [user] {approval.feedback}", err=True)
            else:
                click.echo(f"ozm: denied {display_name}", err=True)
            sys.exit(DENIED)

        audit_log("no-dialog", "run", audit_target, approval.feedback)
        click.echo(f"ozm: [{label}] {display_name}")
        show_file(script)
        if approval.feedback:
            click.echo(f"ozm: dialog error: [ozm] {approval.feedback}", err=True)
        click.echo(
            "ozm: BLOCKED — approval dialog could not be displayed. "
            "The script was NOT executed. "
            "Do NOT retry. "
            "Tell the user ozm needs a macOS GUI session to approve this script.",
            err=True,
        )
        sys.exit(NO_DIALOG)
    finally:
        _cleanup(cleanup_path)


def run_stdin_content(
    content: str,
    args: tuple[str, ...],
    agent,
    *,
    title: str | None = None,
    key_prefix: str = STDIN_PREFIX,
    display_prefix: str = "stdin",
) -> None:
    title = (title or "stdin-script").strip() or "stdin-script"
    if not content:
        raise click.ClickException("stdin script is empty")
    if not content.startswith("#!"):
        raise click.ClickException(
            "stdin script must start with a shebang; use 'ozm bash --command ...' "
            "for raw shell snippets"
        )
    tmp = _write_temp_script(content, title)
    _run_reviewed_script(
        tmp,
        args,
        agent,
        key_target=f"{key_prefix}{title}",
        display_name=f"{display_prefix}:{title}",
        current_hash=compute_content_hash(content),
        cleanup_path=tmp,
    )


@click.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read reviewed script content from stdin.")
@click.option("--title", help="Stable title for --stdin approval cache entries.")
@click.argument("items", nargs=-1, type=click.UNPROCESSED, required=False)
def run_cmd(from_stdin: bool, title: str | None, items: tuple[str, ...]) -> None:
    """Run a script after content review (hash-gated)."""
    parts, agent = extract_agent_metadata(list(items))
    if from_stdin:
        run_stdin_content(sys.stdin.read(), tuple(parts), agent, title=title)
        return
    if title:
        raise click.ClickException("--title is only valid with --stdin")
    if not parts:
        raise click.ClickException("Provide a script to run.")

    script = parts[0]
    args = tuple(parts[1:])

    if not os.path.exists(script):
        raise click.ClickException(f"{script}: not found")
    if not os.path.isfile(script):
        raise click.ClickException(f"{script}: not a file")

    abs_path = resolve_path(script)
    _run_reviewed_script(script, args, agent, key_target=abs_path, display_name=script)


def _status_entries() -> tuple[str, list[dict]]:
    from ozm.config import find_project_root

    root = find_project_root()
    prefix = root + "\0"
    hashes = load_hashes()
    tracked = {k: v for k, v in hashes.items() if k.startswith(prefix)}
    entries = []
    for key, stored_hash in sorted(tracked.items()):
        target = key[len(prefix):]
        display = _display_key_target(root, target)
        added = 0
        removed = 0
        if target.startswith("cmd:"):
            status = "ok"
            kind = "cmd"
        elif target.startswith(STDIN_PREFIX):
            status = "ok"
            kind = "stdin"
        elif target.startswith(SHELL_PREFIX):
            status = "ok"
            kind = "shell"
        elif os.path.exists(target):
            kind = "run"
            current = compute_hash(target)
            if current == stored_hash:
                status = "ok"
            else:
                status = "changed"
                _, added, removed = snapshot_diff(key, target)
        else:
            status = "missing"
            kind = "run"
        entries.append(
            {
                "kind": kind,
                "target": target,
                "display": display,
                "status": status,
                "added": added,
                "removed": removed,
            }
        )
    return root, entries


@click.command("status")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def status_cmd(json_output: bool) -> None:
    """Show tracked files and commands with their approval status."""
    root, entries = _status_entries()
    if json_output:
        click.echo(json.dumps({"project": root, "entries": entries}, sort_keys=True))
        return
    if not entries:
        click.echo("No tracked entries.")
        return
    labels = {"ok": "ok", "changed": "CHANGED", "missing": "MISSING"}
    for entry in entries:
        label = labels[entry["status"]]
        suffix = ""
        if entry["status"] == "changed" and (entry["added"] or entry["removed"]):
            suffix = f"  +{entry['added']} -{entry['removed']}"
        click.echo(f"  [{label:>7}] {entry['display']}{suffix}")


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

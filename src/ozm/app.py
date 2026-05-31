#!/usr/bin/env python3
"""Manage the ozm menu bar app."""

import json
import os
import subprocess
import sys

import click

from ozm.socket_client import SOCKET_PATH, send_request


def _app_path() -> str:
    return os.path.expanduser("~/Applications/ozm.app")


def _dev_binary() -> str | None:
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(src)
    binary = os.path.join(
        repo_root, "app", ".build", "debug", "OzmApp"
    )
    if os.path.isfile(binary):
        return binary
    return None


@click.group("app")
def app_cmd():
    """Manage the ozm menu bar app."""


@app_cmd.command("start")
def app_start():
    """Launch the ozm menu bar app."""
    app = _app_path()
    if os.path.isdir(app):
        subprocess.Popen(["open", app])
        click.echo(f"ozm: launched {app}")
        return

    binary = _dev_binary()
    if binary:
        subprocess.Popen([binary])
        click.echo(f"ozm: launched dev build {binary}")
        return

    click.echo("ozm: app not found. Build with 'ozm app build' first.", err=True)
    sys.exit(1)


@app_cmd.command("stop")
def app_stop():
    """Stop the ozm menu bar app."""
    if not os.path.exists(SOCKET_PATH):
        click.echo("ozm: app is not running (no socket)")
        return

    try:
        subprocess.run(
            ["pkill", "-f", "OzmApp"],
            capture_output=True,
            timeout=5,
        )
        click.echo("ozm: app stopped")
    except (subprocess.TimeoutExpired, OSError) as exc:
        click.echo(f"ozm: could not stop app: {exc}", err=True)
        sys.exit(1)


@app_cmd.command("status")
def app_status():
    """Check if the ozm app is running."""
    if not os.path.exists(SOCKET_PATH):
        click.echo("  app: not running (no socket)")
        return

    import uuid

    resp = send_request({
        "version": 1,
        "id": str(uuid.uuid4()),
        "type": "status",
        "agent": {"name": "ozm-cli", "description": "status check"},
        "payload": {},
    }, timeout=5)

    if resp is None:
        click.echo(f"  socket: {SOCKET_PATH} (stale — not connectable)")
        return

    click.echo(f"  app: running")
    click.echo(f"  socket: {SOCKET_PATH}")

    status = resp.get("feedback")
    if status:
        try:
            info = json.loads(status)
            click.echo(f"  pending: {info.get('pending_count', 0)} approvals")
            agents = info.get("agents", [])
            if agents:
                click.echo(f"  agents: {', '.join(agents)}")
            click.echo(f"  dnd: {'on' if info.get('dnd') else 'off'}")
        except (json.JSONDecodeError, TypeError):
            pass


@app_cmd.command("build")
def app_build():
    """Build the menu bar app from source."""
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(src)
    app_source = os.path.join(repo_root, "app")

    if not os.path.isdir(app_source):
        click.echo("ozm: app source not found", err=True)
        sys.exit(1)

    click.echo("ozm: building app...")
    result = subprocess.run(
        ["swift", "build", "--package-path", app_source, "-c", "release"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        click.echo(f"ozm: build failed:\n{result.stderr}", err=True)
        sys.exit(1)

    click.echo("ozm: build complete")
    binary = os.path.join(app_source, ".build", "release", "OzmApp")
    click.echo(f"  binary: {binary}")

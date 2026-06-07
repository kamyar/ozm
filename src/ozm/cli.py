#!/usr/bin/env python3

import os
import subprocess
from importlib.metadata import version as pkg_version

import click

from ozm.audit import log_cmd
from ozm.run import reset_cmd, run_cmd, status_cmd
from ozm.git import git_cmd
from ozm.install import install_cmd
from ozm.cmd import cmd_cmd
from ozm.app import app_cmd
from ozm.doctor import doctor_cmd


def _get_version() -> str:
    v = pkg_version("ozm")
    if v and v != "0.0.0":
        return v
    src = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h %cs"],
            capture_output=True, text=True, timeout=3, cwd=src,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"dev ({result.stdout.strip()})"
    except (subprocess.TimeoutExpired, OSError):
        pass
    return "dev"


@click.group()
@click.version_option(version=_get_version(), prog_name="ozm")
def cli():
    """Content-aware script execution gate and git rule enforcer."""


@click.command("version")
def version_cmd() -> None:
    """Show ozm version."""
    click.echo(f"ozm {_get_version()}")


@click.command("trust")
def trust_cmd() -> None:
    """Snapshot the in-repo .ozm.yaml into ~/.ozm/projects/ (user-owned)."""
    from ozm.config import OZM_DIR, PROJECTS_DIR, _project_config_path, find_project_root
    from ozm.storage import refuse_symlink, save_bytes_atomic_no_follow

    root = find_project_root()
    repo_config = os.path.join(root, ".ozm.yaml")
    if not os.path.isfile(repo_config):
        raise click.ClickException(f"No .ozm.yaml found in {root}")
    dest = _project_config_path()
    try:
        refuse_symlink(OZM_DIR, "config directory")
        refuse_symlink(PROJECTS_DIR, "config directory")
        refuse_symlink(dest, "config file")
    except RuntimeError as exc:
        if dest in str(exc):
            raise click.ClickException(f"refusing to write through symlink: {dest}") from exc
        raise click.ClickException(str(exc)) from exc
    with open(repo_config, "rb") as f:
        content = f.read()
    try:
        save_bytes_atomic_no_follow(
            dest,
            content,
            directory=PROJECTS_DIR,
            directory_label="config directory",
            parent_directory=OZM_DIR,
            parent_label="config directory",
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"ozm: copied {repo_config} -> {dest}")


TIPS = [
    "Batch your work: instead of running many single commands, if you already "
    "know the sequence of commands you'll need, put them in a script with a "
    "shebang (e.g. #!/usr/bin/env bash) and run it once with 'ozm run <script>'.",
    "Prefer read-only tools. Reach for rg, cat, nl, head, tail, ls, and git "
    "status/log/diff before anything that mutates files or state.",
    "Avoid complex commands. Keep each command simple and single-purpose; "
    "long pipelines and chained operators are hard to review and approve.",
    "Avoid hacky shell wrappers. Things like 'bash -lc ...', inline 'python -c', "
    "or shell expansion ($(...), backticks) look like bypasses and are blocked — "
    "put real logic in a reviewed script and run it with 'ozm run <script>'.",
    "Avoid curl. Install HTTPie with 'uv tool install httpie' and use explicit "
    "methods (e.g. 'http GET <url>', 'http POST <url> key=value'). For complex "
    "requests, write a reviewed Python script using httpx and run it with "
    "'ozm run <script>'.",
]


@click.command("tips")
def tips_cmd() -> None:
    """Show tips for working effectively (and within the rules) under ozm."""
    click.echo("ozm tips — how to work effectively within the execution gate:\n")
    for i, tip in enumerate(TIPS, 1):
        click.echo(f"{i}. {tip}\n")


@click.command("config")
def config_cmd() -> None:
    """Show the path to this project's user-owned config."""
    from ozm.config import _global_config_path, _project_config_path, find_project_root
    root = find_project_root()
    path = _project_config_path()
    global_path = _global_config_path()
    click.echo(f"project: {root}")
    click.echo(f"config:  {path}")
    click.echo(f"global:  {global_path}")
    if os.path.isfile(path):
        click.echo(f"status:  exists")
    else:
        click.echo(f"status:  not found — run 'ozm trust' to import .ozm.yaml")


cli.add_command(run_cmd, "run")
cli.add_command(status_cmd, "status")
cli.add_command(reset_cmd, "reset")
cli.add_command(git_cmd, "git")
cli.add_command(install_cmd, "install")
cli.add_command(cmd_cmd, "cmd")
cli.add_command(log_cmd, "log")
cli.add_command(doctor_cmd, "doctor")
cli.add_command(trust_cmd, "trust")
cli.add_command(config_cmd, "config")
cli.add_command(app_cmd, "app")
cli.add_command(version_cmd, "version")
cli.add_command(tips_cmd, "tips")

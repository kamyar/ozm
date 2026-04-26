#!/usr/bin/env python3

import os
import shutil

import click

from ozm.audit import log_cmd
from ozm.run import reset_cmd, run_cmd, status_cmd
from ozm.git import git_cmd
from ozm.install import install_cmd
from ozm.cmd import cmd_cmd
from ozm.doctor import doctor_cmd


@click.group()
def cli():
    """Content-aware script execution gate and git rule enforcer."""


@click.command("trust")
def trust_cmd() -> None:
    """Snapshot the in-repo .ozm.yaml into ~/.ozm/projects/ (user-owned)."""
    from ozm.config import find_project_root, _project_config_path, PROJECTS_DIR
    root = find_project_root()
    repo_config = os.path.join(root, ".ozm.yaml")
    if not os.path.isfile(repo_config):
        raise click.ClickException(f"No .ozm.yaml found in {root}")
    dest = _project_config_path()
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    shutil.copy2(repo_config, dest)
    click.echo(f"ozm: copied {repo_config} -> {dest}")


@click.command("config")
def config_cmd() -> None:
    """Show the path to this project's user-owned config."""
    from ozm.config import _project_config_path, find_project_root
    root = find_project_root()
    path = _project_config_path()
    click.echo(f"project: {root}")
    click.echo(f"config:  {path}")
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

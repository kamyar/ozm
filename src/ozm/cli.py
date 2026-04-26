#!/usr/bin/env python3

import os

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
    """Trust the .ozm.yaml in the current project."""
    from ozm.config import find_project_root, trust_config, CONFIG_FILE
    root = find_project_root()
    path = os.path.join(root, CONFIG_FILE)
    if not os.path.isfile(path):
        raise click.ClickException(f"No {CONFIG_FILE} found in {root}")
    trust_config(path)
    click.echo(f"ozm: trusted {path}")


cli.add_command(run_cmd, "run")
cli.add_command(status_cmd, "status")
cli.add_command(reset_cmd, "reset")
cli.add_command(git_cmd, "git")
cli.add_command(install_cmd, "install")
cli.add_command(cmd_cmd, "cmd")
cli.add_command(log_cmd, "log")
cli.add_command(doctor_cmd, "doctor")
cli.add_command(trust_cmd, "trust")

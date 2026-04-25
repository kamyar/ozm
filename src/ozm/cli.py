#!/usr/bin/env python3

import click

from ozm.run import reset_cmd, run_cmd, status_cmd
from ozm.git import git_group


@click.group()
def cli():
    """Content-aware script execution gate and git rule enforcer."""


cli.add_command(run_cmd, "run")
cli.add_command(status_cmd, "status")
cli.add_command(reset_cmd, "reset")
cli.add_command(git_group, "git")

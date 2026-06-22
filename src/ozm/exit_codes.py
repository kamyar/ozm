#!/usr/bin/env python3
"""Stable process exit codes for ozm integration consumers."""

import click

OK = 0
GENERAL_ERROR = 1
USAGE_ERROR = 2
BLOCKED = 10
DENIED = 11
NO_DIALOG = 12
CONFIG_ERROR = 13
MISSING_METADATA = 14


def click_error(message: str, exit_code: int) -> click.ClickException:
    exc = click.ClickException(message)
    exc.exit_code = exit_code
    return exc

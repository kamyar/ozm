#!/usr/bin/env python3
"""Shell-free stdout filtering for Ozm command proxies."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import click


def current_grep_terms() -> tuple[str, ...]:
    """Return root-level literal grep terms for the active Click command."""
    context = click.get_current_context(silent=True)
    if context is None:
        return ()
    root = context.find_root()
    terms = root.params.get("grep_terms", ())
    return tuple(terms) if terms else ()


def current_head_lines() -> int | None:
    """Return the root-level stdout line limit for the active command."""
    context = click.get_current_context(silent=True)
    if context is None:
        return None
    return context.find_root().params.get("head_lines")


def output_filter_active() -> bool:
    return bool(current_grep_terms()) or current_head_lines() is not None


def run_with_output_filter(
    argv: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run argv and apply root-level grep and head filters to stdout."""
    terms = current_grep_terms()
    head_lines = current_head_lines()
    if not terms and head_lines is None:
        return subprocess.run(argv, **kwargs)
    if "stdout" in kwargs or "capture_output" in kwargs:
        raise ValueError("stdout filtering cannot be combined with captured output")

    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
        **kwargs,
    )
    matched = False
    emitted = 0
    try:
        if process.stdout is not None:
            for line in process.stdout:
                if terms and not any(term in line for term in terms):
                    continue
                matched = True
                if head_lines is not None and emitted >= head_lines:
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
                emitted += 1
        returncode = process.wait()
    except BaseException:
        process.terminate()
        process.wait()
        raise

    # Match grep behavior when the child succeeded but no output line matched.
    if returncode == 0 and terms and not matched:
        returncode = 1
    return subprocess.CompletedProcess(argv, returncode)

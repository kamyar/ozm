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


def run_with_output_filter(
    argv: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run argv and show only stdout lines that contain a requested term."""
    terms = current_grep_terms()
    if not terms:
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
    try:
        if process.stdout is not None:
            for line in process.stdout:
                if any(term in line for term in terms):
                    matched = True
                    sys.stdout.write(line)
                    sys.stdout.flush()
        returncode = process.wait()
    except BaseException:
        process.terminate()
        process.wait()
        raise

    # Match grep behavior when the child succeeded but no output line matched.
    if returncode == 0 and not matched:
        returncode = 1
    return subprocess.CompletedProcess(argv, returncode)

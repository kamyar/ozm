#!/usr/bin/env python3
"""Resolve helper executables from trusted system locations only.

ozm's policy checks shell out to tools like git and osascript. Resolving
them through the caller-controlled PATH would let an agent substitute fake
binaries (e.g. a git that reports a fake branch to dodge the push policy),
so helpers are looked up in a fixed list of system directories instead.
"""

import os
import shutil

TRUSTED_PATH = os.pathsep.join([
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
])

OSASCRIPT = "/usr/bin/osascript"
DEFAULTS = "/usr/bin/defaults"


def trusted_executable(name: str) -> str | None:
    """Absolute path of name within trusted system dirs, or None."""
    return shutil.which(name, path=TRUSTED_PATH)

#!/usr/bin/env python3
"""OS-native approval dialog for script review."""

import os
import platform
import subprocess
from typing import NamedTuple

ELECTRON_EDITORS = {"code", "cursor", "codium", "code-insiders"}

ELECTRON_PROCESS_NAMES = {
    "code": "Code",
    "code-insiders": "Code - Insiders",
    "cursor": "Cursor",
    "codium": "VSCodium",
}


class ReviewSession(NamedTuple):
    proc: subprocess.Popen
    editor: str | None = None
    filename: str | None = None


def request_approval(script: str, label: str) -> bool | None:
    """Ask the user to review and approve a script via OS-native UI.

    Returns True if approved, False if denied, None if no dialog available.
    """
    if platform.system() == "Darwin":
        return _approve_macos(script, label)
    return None


def _count_lines(path: str) -> int:
    with open(path) as f:
        return sum(1 for _ in f)


def _is_electron_editor(editor: str) -> bool:
    return os.path.basename(editor) in ELECTRON_EDITORS


def _open_for_review(path: str) -> ReviewSession:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")

    if editor and _is_electron_editor(editor):
        basename = os.path.basename(editor)
        proc = subprocess.Popen(
            [editor, "--new-window", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ReviewSession(proc=proc, editor=basename, filename=os.path.basename(path))

    if editor:
        proc = subprocess.Popen(
            [editor, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ReviewSession(proc=proc)

    proc = subprocess.Popen(
        ["qlmanage", "-p", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return ReviewSession(proc=proc)


def _close_review(session: ReviewSession) -> None:
    if session.editor and session.filename:
        process_name = ELECTRON_PROCESS_NAMES.get(session.editor)
        if process_name:
            _close_electron_window(process_name, session.filename)
            return

    session.proc.terminate()
    try:
        session.proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        session.proc.kill()


def _close_electron_window(process_name: str, filename: str) -> None:
    safe_name = filename.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "System Events"\n'
        f'  tell process "{process_name}"\n'
        f'    set w to (first window whose name contains "{safe_name}")\n'
        f'    click (first button of w whose subrole is "AXCloseButton")\n'
        f'    delay 0.5\n'
        f'    if (count of windows) is 0 then\n'
        f'      keystroke "q" using command down\n'
        f'    end if\n'
        f'  end tell\n'
        f'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _approve_macos(script: str, label: str) -> bool | None:
    line_count = _count_lines(script)
    session = _open_for_review(script)

    safe_path = script.replace("\\", "\\\\").replace('"', '\\"')
    dialog_text = (
        f"[{label}] {safe_path}\\n\\n"
        f"{line_count} lines\\n\\n"
        f"The file has been opened for review.\\n"
        f"Allow execution?"
    )

    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                f'display dialog "{dialog_text}" '
                f'buttons {{"Deny", "Allow"}} default button "Deny" '
                f'with title "ozm" with icon caution',
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError):
        _close_review(session)
        return None

    _close_review(session)

    if result.returncode == 0:
        return "button returned:Allow" in result.stdout

    if "user canceled" in result.stderr.lower():
        return False

    return None

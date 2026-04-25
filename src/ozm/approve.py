#!/usr/bin/env python3
"""OS-native approval dialog for script review."""

import os
import platform
import subprocess
import tempfile
from typing import NamedTuple


class ApprovalResult(NamedTuple):
    approved: bool | None
    feedback: str | None = None


def request_approval(script: str, label: str) -> ApprovalResult:
    """Ask the user to review and approve a script via OS-native UI."""
    if platform.system() == "Darwin":
        return _approve_file_macos(script, label)
    return ApprovalResult(approved=None)


def request_cmd_approval(command: str) -> ApprovalResult:
    """Ask the user to approve an arbitrary command via OS-native dialog."""
    if platform.system() == "Darwin":
        return _approve_cmd_macos(command)
    return ApprovalResult(approved=None)


def _count_lines(path: str) -> int:
    with open(path) as f:
        return sum(1 for _ in f)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# NSAlert with a scrollable read-only text view + feedback field
_COCOA_FILE_DIALOG = '''\
use framework "Cocoa"
use scripting additions

current application's NSApplication's sharedApplication()
current application's NSApp's setActivationPolicy:(current application's NSApplicationActivationPolicyRegular)
current application's NSApp's activateIgnoringOtherApps:true

set filePath to POSIX file "__FILEPATH__"
set fileContent to read filePath as «class utf8»

set alert to current application's NSAlert's alloc()'s init()
alert's setMessageText:"__TITLE__"
alert's setInformativeText:"__SUBTITLE__"
alert's setAlertStyle:(current application's NSAlertStyleWarning)
alert's addButtonWithTitle:"Allow"
alert's addButtonWithTitle:"Deny"

set accessory to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, 560, 380))

set scrollView to current application's NSScrollView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 40, 560, 330))
scrollView's setHasVerticalScroller:true
scrollView's setBorderType:(current application's NSBezelBorder)

set contentSize to scrollView's contentSize()
set tv to current application's NSTextView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, contentSize's width, contentSize's height))
tv's setString:fileContent
tv's setEditable:false
tv's setFont:(current application's NSFont's fontWithName:"Menlo" |size|:11)
tv's setMaxSize:{1.0E+7, 1.0E+7}
tv's setVerticallyResizable:true
tv's textContainer()'s setWidthTracksTextView:true
scrollView's setDocumentView:tv
accessory's addSubview:scrollView

set fb to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 5, 560, 24))
fb's setPlaceholderString:"Feedback for the agent..."
accessory's addSubview:fb

alert's setAccessoryView:accessory
alert's |window|()'s setInitialFirstResponder:fb
alert's |window|()'s setLevel:(current application's NSFloatingWindowLevel)

set response to alert's runModal()
set feedback to fb's stringValue() as text

if response = (current application's NSAlertFirstButtonReturn) as integer then
    return "ALLOW:" & feedback
else
    return "DENY:" & feedback
end if
'''


def _parse_cocoa_result(result: subprocess.CompletedProcess) -> ApprovalResult:
    if result.returncode != 0:
        if "user canceled" in result.stderr.lower():
            return ApprovalResult(approved=False)
        return ApprovalResult(approved=None)

    output = result.stdout.strip()
    if output.startswith("ALLOW:"):
        return ApprovalResult(approved=True)
    if output.startswith("DENY:"):
        feedback = output[5:].strip() or None
        return ApprovalResult(approved=False, feedback=feedback)
    return ApprovalResult(approved=None)


def _approve_file_macos(script: str, label: str) -> ApprovalResult:
    abs_path = os.path.abspath(script)
    line_count = _count_lines(script)
    title = f"[{label}] {os.path.basename(script)}"
    subtitle = f"{abs_path} — {line_count} lines"

    applescript = (
        _COCOA_FILE_DIALOG
        .replace("__FILEPATH__", _escape(abs_path))
        .replace("__TITLE__", _escape(title))
        .replace("__SUBTITLE__", _escape(subtitle))
    )

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".applescript", delete=False
        ) as f:
            f.write(applescript)
            tmp = f.name
        try:
            result = subprocess.run(
                ["osascript", tmp],
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            os.unlink(tmp)
    except (subprocess.TimeoutExpired, OSError):
        return ApprovalResult(approved=None)

    return _parse_cocoa_result(result)


def _extract_feedback(stdout: str) -> str | None:
    for part in stdout.strip().split(", "):
        if part.startswith("text returned:"):
            text = part[len("text returned:"):]
            return text if text.strip() else None
    return None


def _approve_cmd_macos(command: str) -> ApprovalResult:
    safe_cmd = _escape(command)
    dialog_text = (
        f"Command:\\n\\n{safe_cmd}\\n\\n"
        f"Allow execution?"
    )

    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                f'display dialog "{dialog_text}" '
                f'default answer "" '
                f'buttons {{"Deny", "Allow"}} default button "Deny" '
                f'with title "ozm" with icon caution',
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ApprovalResult(approved=None)

    if result.returncode == 0:
        stdout = result.stdout
        feedback = _extract_feedback(stdout)
        if "button returned:Allow" in stdout:
            return ApprovalResult(approved=True)
        if "button returned:Deny" in stdout:
            return ApprovalResult(approved=False, feedback=feedback)
        return ApprovalResult(approved=False)

    if "user canceled" in result.stderr.lower():
        return ApprovalResult(approved=False)

    return ApprovalResult(approved=None)

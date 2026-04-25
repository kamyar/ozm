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
    command: str | None = None


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


def _render_rtf(path: str) -> str | None:
    try:
        from pygments import highlight
        from pygments.formatters import RtfFormatter
        from pygments.lexers import get_lexer_for_filename, TextLexer
    except ImportError:
        return None

    with open(path) as f:
        content = f.read()

    try:
        lexer = get_lexer_for_filename(path)
    except Exception:
        lexer = TextLexer()

    formatter = RtfFormatter(fontface="Menlo", fontsize=22)
    return highlight(content, lexer, formatter)


# Plain text content loading for AppleScript
_LOAD_PLAIN = '''\
set filePath to POSIX file "__FILEPATH__"
set fileContent to read filePath as «class utf8»
'''

_SET_PLAIN = '''\
tv's setString:fileContent
tv's setFont:(current application's NSFont's fontWithName:"Menlo" |size|:11)
'''

# RTF content loading for AppleScript
_LOAD_RTF = '''\
set rtfData to current application's NSData's dataWithContentsOfFile:"__RTFPATH__"
'''

_SET_RTF = '''\
set attrString to current application's NSAttributedString's alloc()'s initWithRTF:rtfData documentAttributes:(missing value)
tv's textStorage()'s setAttributedString:attrString
'''

# NSAlert with a scrollable read-only text view + feedback field
_COCOA_FILE_DIALOG = '''\
use framework "Cocoa"
use scripting additions

current application's NSApplication's sharedApplication()
current application's NSApp's setActivationPolicy:(current application's NSApplicationActivationPolicyRegular)
current application's NSApp's activateIgnoringOtherApps:true

__LOAD_CONTENT__

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
tv's setEditable:false
tv's setBackgroundColor:(current application's NSColor's colorWithRed:0.80 green:0.82 blue:0.84 alpha:1.0)
tv's setMaxSize:{1.0E+7, 1.0E+7}
tv's setVerticallyResizable:true
tv's textContainer()'s setWidthTracksTextView:true
__SET_CONTENT__
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
        feedback = output[6:].strip() or None
        return ApprovalResult(approved=True, feedback=feedback)
    if output.startswith("DENY:"):
        feedback = output[5:].strip() or None
        return ApprovalResult(approved=False, feedback=feedback)
    return ApprovalResult(approved=None)


def _approve_file_macos(script: str, label: str) -> ApprovalResult:
    abs_path = os.path.abspath(script)
    line_count = _count_lines(script)
    title = f"[{label}] {os.path.basename(script)}"
    subtitle = f"{abs_path} — {line_count} lines"

    rtf_content = _render_rtf(abs_path)
    rtf_tmp = None

    if rtf_content:
        rtf_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".rtf", delete=False
        )
        rtf_file.write(rtf_content)
        rtf_file.close()
        rtf_tmp = rtf_file.name
        load_section = _LOAD_RTF.replace("__RTFPATH__", _escape(rtf_tmp))
        set_section = _SET_RTF
    else:
        load_section = _LOAD_PLAIN.replace("__FILEPATH__", _escape(abs_path))
        set_section = _SET_PLAIN

    applescript = (
        _COCOA_FILE_DIALOG
        .replace("__LOAD_CONTENT__", load_section)
        .replace("__SET_CONTENT__", set_section)
        .replace("__TITLE__", _escape(title))
        .replace("__SUBTITLE__", _escape(subtitle))
    )

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".applescript", delete=False
        ) as f:
            f.write(applescript)
            script_tmp = f.name
        try:
            result = subprocess.run(
                ["osascript", script_tmp],
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            os.unlink(script_tmp)
    except (subprocess.TimeoutExpired, OSError):
        return ApprovalResult(approved=None)
    finally:
        if rtf_tmp:
            try:
                os.unlink(rtf_tmp)
            except OSError:
                pass

    return _parse_cocoa_result(result)


_COCOA_CMD_DIALOG = '''\
use framework "Cocoa"
use scripting additions

current application's NSApplication's sharedApplication()
current application's NSApp's setActivationPolicy:(current application's NSApplicationActivationPolicyRegular)
current application's NSApp's activateIgnoringOtherApps:true

set alert to current application's NSAlert's alloc()'s init()
alert's setMessageText:"Command"
alert's setInformativeText:"Edit the command if needed, then Allow or Deny."
alert's setAlertStyle:(current application's NSAlertStyleWarning)
alert's addButtonWithTitle:"Allow"
alert's addButtonWithTitle:"Deny"

set accessory to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, 560, 70))

set cmdField to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 35, 560, 24))
cmdField's setStringValue:"__COMMAND__"
cmdField's setFont:(current application's NSFont's fontWithName:"Menlo" |size|:12)
accessory's addSubview:cmdField

set fb to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 5, 560, 24))
fb's setPlaceholderString:"Feedback for the agent..."
accessory's addSubview:fb

alert's setAccessoryView:accessory
alert's |window|()'s setInitialFirstResponder:cmdField
alert's |window|()'s setLevel:(current application's NSFloatingWindowLevel)

set response to alert's runModal()
set editedCmd to cmdField's stringValue() as text
set feedback to fb's stringValue() as text

if response = (current application's NSAlertFirstButtonReturn) as integer then
    return "ALLOW:" & editedCmd & "\\n" & feedback
else
    return "DENY:" & editedCmd & "\\n" & feedback
end if
'''


def _parse_cmd_result(result: subprocess.CompletedProcess) -> ApprovalResult:
    if result.returncode != 0:
        if "user canceled" in result.stderr.lower():
            return ApprovalResult(approved=False)
        return ApprovalResult(approved=None)

    output = result.stdout.strip()
    for prefix, approved in [("ALLOW:", True), ("DENY:", False)]:
        if output.startswith(prefix):
            rest = output[len(prefix):]
            parts = rest.split("\n", 1)
            cmd = parts[0].strip() or None
            feedback = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            return ApprovalResult(approved=approved, feedback=feedback, command=cmd)
    return ApprovalResult(approved=None)


def _approve_cmd_macos(command: str) -> ApprovalResult:
    applescript = _COCOA_CMD_DIALOG.replace("__COMMAND__", _escape(command))

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

    return _parse_cmd_result(result)

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
    allow_pattern: str | None = None


def _get_git_diff(path: str) -> str | None:
    abs_path = os.path.abspath(path)
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color", abs_path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def request_approval(script: str, label: str) -> ApprovalResult:
    """Ask the user to review and approve a script via OS-native UI."""
    diff = _get_git_diff(script) if label == "CHANGED" else None
    if platform.system() == "Darwin":
        return _approve_file_macos(script, label, diff=diff)
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


def _is_dark_mode() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True,
        )
        return "dark" in result.stdout.strip().lower()
    except OSError:
        return False


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

    style = "monokai" if _is_dark_mode() else "default"
    formatter = RtfFormatter(fontface="Menlo", fontsize=22, linenos=True, style=style)
    return highlight(content, lexer, formatter)


def _render_diff_rtf(diff: str) -> str | None:
    try:
        from pygments import highlight
        from pygments.formatters import RtfFormatter
        from pygments.lexers import DiffLexer
    except ImportError:
        return None

    style = "monokai" if _is_dark_mode() else "default"
    formatter = RtfFormatter(fontface="Menlo", fontsize=22, style=style)
    return highlight(diff, DiffLexer(), formatter)


# Plain text content loading for AppleScript
_LOAD_PLAIN = '''\
set filePath to POSIX file "__FILEPATH__"
set rawContent to read filePath as «class utf8»
set theLines to paragraphs of rawContent
set numberedLines to {}
repeat with i from 1 to count of theLines
    set lineNum to text -4 thru -1 of ("    " & i)
    set end of numberedLines to lineNum & "  " & item i of theLines
end repeat
set AppleScript's text item delimiters to linefeed
set fileContent to numberedLines as text
set AppleScript's text item delimiters to ""
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

set cSize to scrollView's contentSize()
set tv to current application's NSTextView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, cSize's width, cSize's height))
tv's setEditable:false
set theAppearance to current application's NSApp's effectiveAppearance()
set appearanceName to (theAppearance's |name|()) as text
if appearanceName contains "Dark" then
    set bgColor to current application's NSColor's colorWithRed:0.20 green:0.22 blue:0.24 alpha:1.0
    tv's setBackgroundColor:bgColor
else
    set bgColor to current application's NSColor's colorWithRed:0.80 green:0.82 blue:0.84 alpha:1.0
    tv's setBackgroundColor:bgColor
end if
tv's setMaxSize:{1.0E+7, 1.0E+7}
tv's setVerticallyResizable:true
set tc to tv's textContainer()
tc's setWidthTracksTextView:true
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
        return ApprovalResult(approved=None, feedback=result.stderr.strip() or None)

    output = result.stdout.strip()
    if output.startswith("ALLOW:"):
        feedback = output[6:].strip() or None
        return ApprovalResult(approved=True, feedback=feedback)
    if output.startswith("DENY:"):
        feedback = output[5:].strip() or None
        return ApprovalResult(approved=False, feedback=feedback)
    return ApprovalResult(approved=None, feedback=f"unexpected output: {output[:200]}")


def _approve_file_macos(script: str, label: str, *, diff: str | None = None) -> ApprovalResult:
    abs_path = os.path.abspath(script)
    line_count = _count_lines(script)
    title = f"[{label}] {os.path.basename(script)}"

    rtf_tmp = None
    diff_tmp = None

    if diff:
        subtitle = f"{abs_path} — diff ({line_count} lines total)"
        diff_rtf = _render_diff_rtf(diff)
        if diff_rtf:
            diff_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".rtf", delete=False
            )
            diff_file.write(diff_rtf)
            diff_file.close()
            diff_tmp = diff_file.name
            load_section = _LOAD_RTF.replace("__RTFPATH__", _escape(diff_tmp))
            set_section = _SET_RTF
        else:
            diff_plain = tempfile.NamedTemporaryFile(
                mode="w", suffix=".diff", delete=False
            )
            diff_plain.write(diff)
            diff_plain.close()
            diff_tmp = diff_plain.name
            load_section = _LOAD_PLAIN.replace("__FILEPATH__", _escape(diff_tmp))
            set_section = _SET_PLAIN
    else:
        subtitle = f"{abs_path} — {line_count} lines"
        rtf_content = _render_rtf(abs_path)
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
        for tmp in (rtf_tmp, diff_tmp):
            if tmp:
                try:
                    os.unlink(tmp)
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
alert's setInformativeText:"Edit the command or add an allowlist pattern."
alert's setAlertStyle:(current application's NSAlertStyleWarning)
alert's addButtonWithTitle:"Allow"
alert's addButtonWithTitle:"Deny"

set accessory to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, 560, 190))

set cmdLabel to current application's NSTextField's labelWithString:"Run:"
cmdLabel's setFrame:(current application's NSMakeRect(0, 167, 560, 16))
cmdLabel's setFont:(current application's NSFont's systemFontOfSize:11)
cmdLabel's setTextColor:(current application's NSColor's secondaryLabelColor())
accessory's addSubview:cmdLabel

set cmdScroll to current application's NSScrollView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 90, 560, 75))
cmdScroll's setHasVerticalScroller:true
cmdScroll's setBorderType:(current application's NSBezelBorder)
set cmdSize to cmdScroll's contentSize()
set cmdField to current application's NSTextView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, cmdSize's width, cmdSize's height))
cmdField's setFont:(current application's NSFont's fontWithName:"Menlo" |size|:12)
cmdField's setString:"__COMMAND__"
cmdField's setVerticallyResizable:true
set cmdTc to cmdField's textContainer()
cmdTc's setWidthTracksTextView:true
cmdScroll's setDocumentView:cmdField
accessory's addSubview:cmdScroll

set patLabel to current application's NSTextField's labelWithString:"Allow pattern (saved to .ozm.yaml):"
patLabel's setFrame:(current application's NSMakeRect(0, 67, 560, 16))
patLabel's setFont:(current application's NSFont's systemFontOfSize:11)
patLabel's setTextColor:(current application's NSColor's secondaryLabelColor())
accessory's addSubview:patLabel

set patField to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 42, 560, 24))
patField's setPlaceholderString:"e.g. curl httpbin.org/*"
patField's setFont:(current application's NSFont's fontWithName:"Menlo" |size|:12)
accessory's addSubview:patField

set fb to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 5, 560, 24))
fb's setPlaceholderString:"Feedback for the agent..."
accessory's addSubview:fb

alert's setAccessoryView:accessory
alert's |window|()'s setInitialFirstResponder:cmdField
alert's |window|()'s setLevel:(current application's NSFloatingWindowLevel)

set response to alert's runModal()
set editedCmd to cmdField's |string|() as text
set pattern to patField's stringValue() as text
set feedback to fb's stringValue() as text
set sep to "%%OZM_SEP%%"

if response = (current application's NSAlertFirstButtonReturn) as integer then
    return "ALLOW:" & editedCmd & sep & pattern & sep & feedback
else
    return "DENY:" & editedCmd & sep & pattern & sep & feedback
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
            parts = rest.split("%%OZM_SEP%%", 2)
            cmd = parts[0].replace("\n", " ").strip() or None
            pattern = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            feedback = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
            return ApprovalResult(approved=approved, feedback=feedback, command=cmd, allow_pattern=pattern)
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

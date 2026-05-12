#!/usr/bin/env python3
"""OS-native approval dialog for script review."""

import os
import platform
import subprocess
import tempfile
import unicodedata
from typing import NamedTuple

from ozm.agent import AgentMetadata, extract_agent_metadata_from_command


def _secure_tmpfile(suffix: str, content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


class ApprovalResult(NamedTuple):
    approved: bool | None
    feedback: str | None = None
    command: str | None = None
    allow_pattern: str | None = None
    block_pattern: str | None = None
    apply_globally: bool = False


def _get_git_diff(path: str) -> str | None:
    abs_path = os.path.abspath(path)
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color", "--no-ext-diff", abs_path],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def request_approval(script: str, label: str, agent: AgentMetadata) -> ApprovalResult:
    """Ask the user to review and approve a script via OS-native UI."""
    diff = _get_git_diff(script) if label == "CHANGED" else None
    if platform.system() == "Darwin":
        return _approve_file_macos(script, label, agent, diff=diff)
    return ApprovalResult(approved=None)


def request_cmd_approval(
    command: str,
    agent: AgentMetadata | None = None,
) -> ApprovalResult:
    """Ask the user to approve an arbitrary command via OS-native dialog."""
    command, extracted = extract_agent_metadata_from_command(command)
    if extracted is not None:
        agent = extracted
    if agent is None:
        agent = AgentMetadata(
            name="Command approval",
            description="Review this command before execution.",
        )
    if platform.system() == "Darwin":
        return _approve_cmd_macos(command, agent)
    return ApprovalResult(approved=None)


def request_override(
    command: str,
    violation: str,
    reason: str,
    agent: AgentMetadata,
) -> ApprovalResult:
    """Ask the user for a one-time override of a blocked operation."""
    if platform.system() == "Darwin":
        return _override_macos(command, violation, reason, agent)
    return ApprovalResult(approved=None)


def _count_lines(path: str) -> int:
    with open(path) as f:
        return sum(1 for _ in f)


def _strip_unicode_control(s: str) -> str:
    return "".join(c for c in s if unicodedata.category(c) not in ("Cf", "Cc", "Mn") or c in ("\n", "\t"))


def _escape(s: str) -> str:
    s = _strip_unicode_control(s)
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _agent_context(agent: AgentMetadata) -> str:
    return f"Agent: {agent.name} — {agent.description}"


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

# Standard Edit menu so Cmd+C/V/A/X work in all dialogs
_EDIT_MENU = '''\
set menuBar to current application's NSMenu's alloc()'s init()
set editMenuItem to current application's NSMenuItem's alloc()'s init()
menuBar's addItem:editMenuItem
set editMenu to current application's NSMenu's alloc()'s initWithTitle:"Edit"
set cutItem to (current application's NSMenuItem's alloc()'s initWithTitle:"Cut" action:"cut:" keyEquivalent:"x")
set copyItem to (current application's NSMenuItem's alloc()'s initWithTitle:"Copy" action:"copy:" keyEquivalent:"c")
set pasteItem to (current application's NSMenuItem's alloc()'s initWithTitle:"Paste" action:"paste:" keyEquivalent:"v")
set selectAllItem to (current application's NSMenuItem's alloc()'s initWithTitle:"Select All" action:"selectAll:" keyEquivalent:"a")
editMenu's addItem:cutItem
editMenu's addItem:copyItem
editMenu's addItem:pasteItem
editMenu's addItem:selectAllItem
editMenuItem's setSubmenu:editMenu
current application's NSApp's setMainMenu:menuBar
'''

# NSAlert with a scrollable read-only text view + feedback field
_COCOA_FILE_DIALOG = '''\
use framework "Cocoa"
use scripting additions

current application's NSApplication's sharedApplication()
current application's NSApp's setActivationPolicy:(current application's NSApplicationActivationPolicyRegular)
current application's NSApp's activateIgnoringOtherApps:true

''' + _EDIT_MENU + '''\

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


def _approve_file_macos(
    script: str,
    label: str,
    agent: AgentMetadata,
    *,
    diff: str | None = None,
) -> ApprovalResult:
    abs_path = os.path.abspath(script)
    line_count = _count_lines(script)
    title = f"[{label}] {agent.name}"
    agent_context = _agent_context(agent)

    rtf_tmp = None
    diff_tmp = None

    if diff:
        subtitle = f"{agent_context} — {abs_path} — diff ({line_count} lines total)"
        diff_rtf = _render_diff_rtf(diff)
        if diff_rtf:
            diff_tmp = _secure_tmpfile(".rtf", diff_rtf)
            load_section = _LOAD_RTF.replace("__RTFPATH__", _escape(diff_tmp))
            set_section = _SET_RTF
        else:
            diff_tmp = _secure_tmpfile(".diff", diff)
            load_section = _LOAD_PLAIN.replace("__FILEPATH__", _escape(diff_tmp))
            set_section = _SET_PLAIN
    else:
        subtitle = f"{agent_context} — {abs_path} — {line_count} lines"
        rtf_content = _render_rtf(abs_path)
        if rtf_content:
            rtf_tmp = _secure_tmpfile(".rtf", rtf_content)
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
        script_tmp = _secure_tmpfile(".applescript", applescript)
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

''' + _EDIT_MENU + '''\

set alert to current application's NSAlert's alloc()'s init()
alert's setMessageText:"Command"
alert's setInformativeText:"Edit the command or add an allow/block rule."
alert's setAlertStyle:(current application's NSAlertStyleWarning)
alert's addButtonWithTitle:"Allow"
alert's addButtonWithTitle:"Deny"

set accessory to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, 560, 291))

set theAppearance to current application's NSApp's effectiveAppearance()
set appearanceName to (theAppearance's |name|()) as text
if appearanceName contains "Dark" then
    set metaBgColor to current application's NSColor's colorWithRed:0.14 green:0.16 blue:0.19 alpha:1.0
    set nameBgColor to current application's NSColor's colorWithRed:0.20 green:0.28 blue:0.38 alpha:1.0
    set descBgColor to current application's NSColor's colorWithRed:0.18 green:0.30 blue:0.23 alpha:1.0
    set nameTextColor to current application's NSColor's colorWithRed:0.78 green:0.88 blue:1.0 alpha:1.0
    set descTextColor to current application's NSColor's colorWithRed:0.80 green:0.95 blue:0.84 alpha:1.0
else
    set metaBgColor to current application's NSColor's colorWithRed:0.96 green:0.98 blue:1.0 alpha:1.0
    set nameBgColor to current application's NSColor's colorWithRed:0.88 green:0.94 blue:1.0 alpha:1.0
    set descBgColor to current application's NSColor's colorWithRed:0.90 green:0.98 blue:0.92 alpha:1.0
    set nameTextColor to current application's NSColor's colorWithRed:0.12 green:0.24 blue:0.45 alpha:1.0
    set descTextColor to current application's NSColor's colorWithRed:0.10 green:0.34 blue:0.18 alpha:1.0
end if

set metaPanel to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 221, 560, 64))
metaPanel's setWantsLayer:true
metaPanel's layer()'s setBackgroundColor:(metaBgColor's CGColor())
metaPanel's layer()'s setCornerRadius:8
accessory's addSubview:metaPanel

set nameChip to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(8, 35, 544, 22))
nameChip's setWantsLayer:true
nameChip's layer()'s setBackgroundColor:(nameBgColor's CGColor())
nameChip's layer()'s setCornerRadius:6
metaPanel's addSubview:nameChip

set descChip to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(8, 8, 544, 22))
descChip's setWantsLayer:true
descChip's layer()'s setBackgroundColor:(descBgColor's CGColor())
descChip's layer()'s setCornerRadius:6
metaPanel's addSubview:descChip

set agentLabel to current application's NSTextField's labelWithString:"__AGENT_NAME__"
agentLabel's setFrame:(current application's NSMakeRect(16, 38, 528, 16))
agentLabel's setFont:(current application's NSFont's boldSystemFontOfSize:12)
agentLabel's setTextColor:nameTextColor
metaPanel's addSubview:agentLabel

set descLabel to current application's NSTextField's labelWithString:"__AGENT_DESCRIPTION__"
descLabel's setFrame:(current application's NSMakeRect(16, 11, 528, 16))
descLabel's setFont:(current application's NSFont's systemFontOfSize:11)
descLabel's setTextColor:descTextColor
metaPanel's addSubview:descLabel

set cmdLabel to current application's NSTextField's labelWithString:"Run:"
cmdLabel's setFrame:(current application's NSMakeRect(0, 192, 560, 16))
cmdLabel's setFont:(current application's NSFont's systemFontOfSize:11)
cmdLabel's setTextColor:(current application's NSColor's secondaryLabelColor())
accessory's addSubview:cmdLabel

set cmdScroll to current application's NSScrollView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 115, 560, 75))
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

set patLabel to current application's NSTextField's labelWithString:"Rule pattern (blank + Apply globally uses exact command):"
patLabel's setFrame:(current application's NSMakeRect(0, 92, 560, 16))
patLabel's setFont:(current application's NSFont's systemFontOfSize:11)
patLabel's setTextColor:(current application's NSColor's secondaryLabelColor())
accessory's addSubview:patLabel

set patField to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 67, 560, 24))
patField's setPlaceholderString:"e.g. curl httpbin.org/*"
patField's setFont:(current application's NSFont's fontWithName:"Menlo" |size|:12)
accessory's addSubview:patField

set globalBox to current application's NSButton's alloc()'s initWithFrame:(current application's NSMakeRect(0, 39, 560, 20))
globalBox's setButtonType:(current application's NSButtonTypeSwitch)
globalBox's setTitle:"Apply globally"
globalBox's setFrame:(current application's NSMakeRect(0, 39, 560, 20))
globalBox's setFont:(current application's NSFont's systemFontOfSize:12)
globalBox's setAllowsMixedState:false
accessory's addSubview:globalBox

set fb to current application's NSTextField's alloc()'s initWithFrame:(current application's NSMakeRect(0, 5, 560, 24))
fb's setPlaceholderString:"Feedback for the agent..."
accessory's addSubview:fb

alert's setAccessoryView:accessory
alert's |window|()'s setInitialFirstResponder:cmdField
alert's |window|()'s setLevel:(current application's NSFloatingWindowLevel)

set response to alert's runModal()
set editedCmd to cmdField's |string|() as text
set pattern to patField's stringValue() as text
set globalRule to "0"
if (globalBox's integerValue() as integer) = 1 then
    set globalRule to "1"
end if
set feedback to fb's stringValue() as text
set sep to "%%OZM_SEP%%"

if response = (current application's NSAlertFirstButtonReturn) as integer then
    return "ALLOW:" & editedCmd & sep & pattern & sep & globalRule & sep & feedback
else
    return "DENY:" & editedCmd & sep & pattern & sep & globalRule & sep & feedback
end if
'''


def _parse_cmd_result(result: subprocess.CompletedProcess) -> ApprovalResult:
    if result.returncode != 0:
        if "user canceled" in result.stderr.lower():
            return ApprovalResult(approved=False)
        return ApprovalResult(approved=None, feedback=result.stderr.strip() or None)

    output = result.stdout.strip()
    for prefix, approved in [("ALLOW:", True), ("DENY:", False)]:
        if output.startswith(prefix):
            rest = output[len(prefix):]
            parts = rest.split("%%OZM_SEP%%")
            if len(parts) != 4:
                return ApprovalResult(
                    approved=None,
                    feedback="malformed command dialog output",
                )
            raw_cmd = parts[0]
            if "\n" in raw_cmd or "\r" in raw_cmd:
                return ApprovalResult(
                    approved=None,
                    feedback="edited command must be one line",
                )
            cmd = raw_cmd.strip()
            if not cmd:
                return ApprovalResult(
                    approved=None,
                    feedback="edited command is empty",
                )
            pattern = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            apply_globally = False
            global_marker = parts[2].strip()
            if global_marker not in ("0", "1"):
                return ApprovalResult(
                    approved=None,
                    feedback="malformed command dialog output",
                )
            apply_globally = global_marker == "1"
            feedback = parts[3].strip() if parts[3].strip() else None
            allow_pattern = pattern if approved else None
            block_pattern = pattern if approved is False else None
            return ApprovalResult(
                approved=approved,
                feedback=feedback,
                command=cmd,
                allow_pattern=allow_pattern,
                block_pattern=block_pattern,
                apply_globally=apply_globally,
            )
    return ApprovalResult(approved=None, feedback="unexpected command dialog output")


def _approve_cmd_macos(command: str, agent: AgentMetadata) -> ApprovalResult:
    applescript = (
        _COCOA_CMD_DIALOG
        .replace("__COMMAND__", _escape(command))
        .replace("__AGENT_NAME__", _escape(agent.name))
        .replace("__AGENT_DESCRIPTION__", _escape(agent.description))
    )

    try:
        tmp = _secure_tmpfile(".applescript", applescript)
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


_COCOA_OVERRIDE_DIALOG = '''\
use framework "Cocoa"
use scripting additions

current application's NSApplication's sharedApplication()
current application's NSApp's setActivationPolicy:(current application's NSApplicationActivationPolicyRegular)
current application's NSApp's activateIgnoringOtherApps:true

''' + _EDIT_MENU + '''\

set alert to current application's NSAlert's alloc()'s init()
alert's setMessageText:"Override: __AGENT_NAME__"
alert's setInformativeText:"__AGENT_DESCRIPTION__ — __VIOLATION__ — __COMMAND__"
alert's setAlertStyle:(current application's NSAlertStyleCritical)
alert's addButtonWithTitle:"Allow Once"
alert's addButtonWithTitle:"Deny"

set accessory to current application's NSView's alloc()'s initWithFrame:(current application's NSMakeRect(0, 0, 560, 105))

set reasonLabel to current application's NSTextField's labelWithString:"Agent reasoning:"
reasonLabel's setFrame:(current application's NSMakeRect(0, 82, 560, 16))
reasonLabel's setFont:(current application's NSFont's systemFontOfSize:11)
reasonLabel's setTextColor:(current application's NSColor's secondaryLabelColor())
accessory's addSubview:reasonLabel

set reasonField to current application's NSTextField's wrappingLabelWithString:"__REASON__"
reasonField's setFrame:(current application's NSMakeRect(0, 35, 560, 45))
reasonField's setFont:(current application's NSFont's systemFontOfSize:12)
accessory's addSubview:reasonField

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


def _override_macos(
    command: str,
    violation: str,
    reason: str,
    agent: AgentMetadata,
) -> ApprovalResult:
    applescript = (
        _COCOA_OVERRIDE_DIALOG
        .replace("__VIOLATION__", _escape(violation))
        .replace("__COMMAND__", _escape(command))
        .replace("__REASON__", _escape(reason))
        .replace("__AGENT_NAME__", _escape(agent.name))
        .replace("__AGENT_DESCRIPTION__", _escape(agent.description))
    )

    try:
        tmp = _secure_tmpfile(".applescript", applescript)
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

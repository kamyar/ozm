#!/usr/bin/env python3

import shlex
import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Built-in read tests",
    "--agent-description", "Exercise conservative local read classification.",
]


class BuiltInReadOnlyReasonTests(unittest.TestCase):
    def test_proven_read_forms_are_classified(self):
        cases = {
            ("command", "-v", "git", "gh"): "command lookup",
            ("bazel", "query", "//nodes/order/cart:all"): "bazel query",
            ("brew", "search", "buildbuddy"): "brew search",
            ("npm", "view", "pi-subagents", "version"): "npm view",
            ("npm", "list", "--depth=0"): "npm list",
        }
        for argv, expected in cases.items():
            with self.subTest(argv=argv):
                self.assertEqual(
                    cmd_mod._builtin_read_only_reason(list(argv)),
                    expected,
                )

    def test_trusted_tool_read_forms_are_classified(self):
        cases = {
            ("pi", "--list-models"): "trusted pi model list",
            ("pi", "--list-models", "fable"): "trusted pi model list",
            ("pi", "models", "--help"): "trusted pi metadata",
            ("pi", "--version"): "trusted pi metadata",
            ("df", "-h", "/"): "trusted filesystem usage",
            ("docker", "system", "df"): "trusted docker disk usage",
            ("docker", "builder", "prune", "--help"): "trusted docker help",
            ("bazel", "help"): "trusted bazel help",
            ("orb", "--help"): "trusted orb help",
            ("orbctl", "docker", "--help"): "trusted orbctl help",
            ("go", "env", "GOMODCACHE"): "trusted go environment metadata",
            ("printenv", "PI_MODEL"): "trusted Pi environment metadata",
        }
        with patch.object(
            cmd_mod,
            "trusted_executable",
            side_effect=lambda name: f"/trusted/{name}",
        ):
            for argv, expected in cases.items():
                with self.subTest(argv=argv):
                    self.assertEqual(
                        cmd_mod._trusted_read_only_reason(list(argv)),
                        expected,
                    )

    def test_trusted_tool_write_or_secret_forms_are_not_classified(self):
        cases = [
            ["pi", "update", "--models"],
            ["pi", "--list-models", "--json", "/tmp/output"],
            ["docker", "builder", "prune"],
            ["docker", "run", "image", "--help"],
            ["go", "env", "-w", "GOPROXY=value"],
            ["printenv", "AWS_SECRET_ACCESS_KEY"],
            ["printenv", "PI_TOKEN"],
        ]
        with patch.object(
            cmd_mod,
            "trusted_executable",
            side_effect=lambda name: f"/trusted/{name}",
        ):
            for argv in cases:
                with self.subTest(argv=argv):
                    self.assertIsNone(cmd_mod._trusted_read_only_reason(argv))

    def test_untrusted_absolute_tool_path_is_not_classified(self):
        with patch.object(
            cmd_mod,
            "trusted_executable",
            return_value="/opt/homebrew/bin/pi",
        ):
            self.assertIsNone(
                cmd_mod._trusted_read_only_reason(["/tmp/pi", "--list-models"])
            )

    def test_unknown_or_write_capable_forms_are_not_classified(self):
        cases = [
            ["command", "git"],
            ["command", "-v", "--help"],
            ["bazel", "build", "//:all"],
            ["bazel", "query", "//:all", "--output_file", "/tmp/query"],
            ["bazel", "query", "//:all", "--output_file=/tmp/query"],
            ["brew", "install", "buildbuddy"],
            ["npm", "install", "pi-subagents"],
            ["npm", "exec", "tool"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertIsNone(cmd_mod._builtin_read_only_reason(argv))


class BuiltInReadOnlyExecutionTests(unittest.TestCase):
    def test_proven_read_bypasses_config_cache_and_approval(self):
        args = ["command", "-v", "git"]
        completed = subprocess.CompletedProcess(args, 0)

        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed") as is_allowed, \
             patch.object(cmd_mod, "load_hashes") as load_hashes, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "_run_command", return_value=completed) as run_command, \
             patch.object(cmd_mod, "audit_log") as audit_log:
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, *args])

        self.assertEqual(result.exit_code, 0, result.output)
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()
        run_command.assert_called_once_with(args)
        audit_log.assert_called_once_with(
            "semantic",
            "cmd",
            shlex.join(args),
            "command lookup",
        )

    def test_trusted_read_executes_the_resolved_binary(self):
        args = ["pi", "--list-models", "fable"]
        completed = subprocess.CompletedProcess(args, 0)

        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(
                 cmd_mod,
                 "trusted_executable",
                 return_value="/opt/homebrew/bin/pi",
             ), \
             patch.object(cmd_mod, "is_command_allowed") as is_allowed, \
             patch.object(cmd_mod, "load_hashes") as load_hashes, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "_run_command", return_value=completed) as run_command, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, *args])

        self.assertEqual(result.exit_code, 0, result.output)
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()
        run_command.assert_called_once_with(args, trusted_first=True)

    def test_unknown_form_keeps_normal_approval(self):
        args = ["npm", "install", "package"]

        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=ApprovalResult(approved=False),
             ) as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, *args])

        self.assertEqual(result.exit_code, cmd_mod.DENIED)
        request_approval.assert_called_once()


if __name__ == "__main__":
    unittest.main()

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

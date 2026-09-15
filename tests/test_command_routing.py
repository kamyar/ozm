#!/usr/bin/env python3

import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import command_routing as routing_mod
from ozm import shell as shell_mod


META = [
    "--agent-name", "Routing tests",
    "--agent-description", "Exercise safer installed command routing.",
]


class Example inspectionRoutingTests(unittest.TestCase):
    def test_detects_supported_example_inspect_wrappers(self):
        cases = [
            (
                ["python3", "-m", "example_inspect", "example_inspect_query", "--help"],
                ["example_inspect_query", "--help"],
            ),
            (
                [
                    "uv", "run", "--quiet", "--with", "httpx>=0.27.0",
                    "python", "-m", "example_inspect", "example_inspect_describe",
                ],
                ["example_inspect_describe"],
            ),
            (
                [
                    "env", "PYTHONPATH=/plugin/bin",
                    "/plugin/bin/example_inspect.py", "--help",
                ],
                ["--help"],
            ),
            (
                ["/plugin/bin/example_inspect.py", "example_inspect_query"],
                ["example_inspect_query"],
            ),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(
                    routing_mod.example_inspect_wrapper_args(argv),
                    expected,
                )

    def test_does_not_redirect_direct_or_unrelated_modules(self):
        self.assertIsNone(
            routing_mod.example_inspect_wrapper_args(["example_inspect", "--help"])
        )
        self.assertIsNone(
            routing_mod.example_inspect_wrapper_args(["python3", "-m", "other"])
        )

    def test_cmd_redirects_wrapper_before_policy_or_approval(self):
        with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [
                    *META,
                    "--",
                    "python3", "-m", "example_inspect",
                    "example_inspect_query", "--help",
                ],
            )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        self.assertIn("installed CLI", result.output)
        self.assertIn("ozm cmd", result.output)
        self.assertIn("example_inspect example_inspect_query --help", result.output)
        is_blocked.assert_not_called()
        request_approval.assert_not_called()

    def test_generated_shell_redirects_wrapper_before_script_review(self):
        with patch.object(cmd_mod, "audit_log"), \
             patch("ozm.run.audit_log"), \
             patch("ozm.run.request_approval") as request_approval:
            result = CliRunner().invoke(
                shell_mod.shell_cmd,
                [
                    "--command",
                    "python3 -m example_inspect example_inspect_describe --help",
                    *META,
                ],
            )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        self.assertIn("unnecessary Python or uv wrapper", result.output)
        self.assertIn("example_inspect example_inspect_describe --help", result.output)
        request_approval.assert_not_called()


if __name__ == "__main__":
    unittest.main()

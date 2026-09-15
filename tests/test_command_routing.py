#!/usr/bin/env python3

import shlex
import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import command_routing as routing_mod
from ozm import shell as shell_mod
from ozm.rule_packs import EntrypointRedirectRule


META = [
    "--agent-name", "Routing tests",
    "--agent-description", "Exercise private installed command routing.",
]


def example_rule() -> EntrypointRedirectRule:
    return EntrypointRedirectRule(
        source="work",
        rule_id="example-inspect-entrypoint",
        target="example-inspect",
        python_modules=("example_inspect",),
        script_basenames=("example-inspect.py",),
        guidance="Invoke the installed example inspection entry point directly.",
    )


class PrivateEntrypointRoutingTests(unittest.TestCase):
    def test_detects_supported_configured_wrappers(self):
        cases = [
            (
                ["python3", "-m", "example_inspect", "events", "--help"],
                ("events", "--help"),
            ),
            (
                [
                    "uv", "run", "--quiet", "--with", "httpx>=0.27.0",
                    "python", "-m", "example_inspect", "describe",
                ],
                ("describe",),
            ),
            (
                [
                    "env", "PYTHONPATH=/plugin/bin",
                    "/plugin/bin/example-inspect.py", "--help",
                ],
                ("--help",),
            ),
            (
                ["/plugin/bin/example-inspect.py", "query"],
                ("query",),
            ),
        ]
        with patch.object(
            routing_mod,
            "load_entrypoint_redirects",
            return_value=(example_rule(),),
        ):
            for argv, expected in cases:
                with self.subTest(argv=argv):
                    redirect = routing_mod.configured_entrypoint_redirect(argv)
                    self.assertIsNotNone(redirect)
                    self.assertEqual(redirect.arguments, expected)
                    self.assertEqual(redirect.rule.target, "example-inspect")

    def test_does_not_redirect_direct_or_unrelated_modules(self):
        with patch.object(
            routing_mod,
            "load_entrypoint_redirects",
            return_value=(example_rule(),),
        ):
            self.assertIsNone(
                routing_mod.configured_entrypoint_redirect(
                    ["example-inspect", "--help"]
                )
            )
            self.assertIsNone(
                routing_mod.configured_entrypoint_redirect(
                    ["python3", "-m", "other"]
                )
            )
            self.assertIsNone(
                routing_mod.configured_entrypoint_redirect(
                    ["uv", "run", "--", "echo", "python", "-m", "example_inspect"]
                )
            )
            self.assertIsNone(
                routing_mod.configured_entrypoint_redirect(
                    ["uv", "run", "echo", "python", "-m", "example_inspect"]
                )
            )

    def test_cmd_redirects_wrapper_before_policy_or_approval(self):
        with patch.object(
                 routing_mod,
                 "load_entrypoint_redirects",
                 return_value=(example_rule(),),
             ), patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [
                    *META,
                    "--",
                    "python3", "-m", "example_inspect",
                    "events", "--help",
                ],
            )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        self.assertIn("work/example-inspect-entrypoint", result.output)
        self.assertIn("installed example inspection entry point", result.output)
        self.assertIn("ozm cmd", result.output)
        self.assertIn("example-inspect events --help", result.output)
        is_blocked.assert_not_called()
        request_approval.assert_not_called()

    def test_suggested_help_argument_reaches_target_command(self):
        redirect = routing_mod.EntrypointRedirect(
            rule=example_rule(),
            arguments=("events", "--help"),
        )
        suggestion = routing_mod.entrypoint_suggestion(
            redirect,
            cmd_mod.extract_agent_metadata(list(META))[1],
        )
        suggestion_args = shlex.split(suggestion)
        self.assertEqual(suggestion_args[-4:], ["--", "example-inspect", "events", "--help"])

        with patch.object(
                 routing_mod,
                 "load_entrypoint_redirects",
                 return_value=(example_rule(),),
             ), patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=True), \
             patch.object(
                 cmd_mod,
                 "_run_command",
                 return_value=subprocess.CompletedProcess([], 0),
             ) as run_command, patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, suggestion_args[2:])

        self.assertEqual(result.exit_code, 0, result.output)
        run_command.assert_called_once_with(
            ["example-inspect", "events", "--help"]
        )

    def test_generated_shell_redirects_wrapper_before_script_review(self):
        with patch(
                 "ozm.run.load_entrypoint_redirects",
                 return_value=(example_rule(),),
             ), patch.object(cmd_mod, "audit_log"), \
             patch("ozm.run.audit_log"), \
             patch("ozm.run.request_approval") as request_approval:
            result = CliRunner().invoke(
                shell_mod.shell_cmd,
                [
                    "--command",
                    "python3 -m example_inspect describe --help",
                    *META,
                ],
            )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        self.assertIn("work/example-inspect-entrypoint", result.output)
        self.assertIn("example-inspect describe --help", result.output)
        request_approval.assert_not_called()

    def test_generated_pipeline_cannot_bypass_private_redirect(self):
        with patch(
                 "ozm.run.load_entrypoint_redirects",
                 return_value=(example_rule(),),
             ), patch("ozm.run.audit_log"), \
             patch("ozm.run.request_approval") as request_approval:
            result = CliRunner().invoke(
                shell_mod.shell_cmd,
                [
                    "--command",
                    "python3 -m example_inspect describe | cat",
                    *META,
                ],
            )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        self.assertIn("work/example-inspect-entrypoint", result.output)
        request_approval.assert_not_called()

    def test_generated_shell_fails_closed_when_pack_is_invalid(self):
        with patch(
                 "ozm.run.load_entrypoint_redirects",
                 side_effect=RuntimeError("bad private pack"),
             ), patch("ozm.run.audit_log"), \
             patch("ozm.run.request_approval") as request_approval:
            result = CliRunner().invoke(
                shell_mod.shell_cmd,
                ["--command", "printf '%s\\n' \"$HOME\"", *META],
            )

        self.assertEqual(result.exit_code, cmd_mod.CONFIG_ERROR, result.output)
        self.assertIn("bad private pack", result.output)
        self.assertIn("NOT executed", result.output)
        request_approval.assert_not_called()

    def test_rule_pack_error_fails_closed_before_policy(self):
        with patch.object(
                 routing_mod,
                 "load_entrypoint_redirects",
                 side_effect=RuntimeError("bad private pack"),
             ), patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "printf", "ok"],
            )

        self.assertEqual(result.exit_code, cmd_mod.CONFIG_ERROR, result.output)
        self.assertIn("bad private pack", result.output)
        self.assertIn("NOT executed", result.output)
        is_blocked.assert_not_called()
        request_approval.assert_not_called()


if __name__ == "__main__":
    unittest.main()

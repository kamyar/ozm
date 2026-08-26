#!/usr/bin/env python3

import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import gh as gh_mod
from ozm.approve import ApprovalResult


META = [
    "--agent-name", "GitHub proxy test",
    "--agent-description", "Exercise the operation-aware GitHub proxy.",
]


class GitHubProxyTests(unittest.TestCase):
    def test_proxy_uses_normal_policy_for_semantic_rest_read(self):
        args = ["gh", "api", "rate_limit", "--jq", ".rate.remaining"]
        completed = subprocess.CompletedProcess(args=args, returncode=0)

        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False) as is_allowed, \
             patch.object(cmd_mod, "load_hashes", return_value={}) as load_hashes, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(
                 cmd_mod,
                 "_run_command",
                 return_value=completed,
             ) as run_command, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                gh_mod.gh_cmd,
                [*META, "api", "rate_limit", "--jq", ".rate.remaining"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()
        run_command.assert_called_once_with(args)
        self.assertIn("allowed (github rest GET)", result.output)

    def test_proxy_routes_write_to_normal_approval(self):
        args = ["gh", "pr", "comment", "123", "--body", "hello"]

        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=ApprovalResult(approved=False),
             ) as request_approval, \
             patch.object(cmd_mod, "_run_command") as run_command, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                gh_mod.gh_cmd,
                [*META, "pr", "comment", "123", "--body", "hello"],
            )

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_called_once()
        run_command.assert_not_called()
        self.assertIn("denied cmd", result.output)

    def test_proxy_requires_agent_metadata(self):
        result = CliRunner().invoke(gh_mod.gh_cmd, ["api", "rate_limit"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--agent-name", result.output)
        self.assertIn("--agent-description", result.output)

    def test_proxy_help_explains_read_and_write_policy(self):
        result = CliRunner().invoke(gh_mod.gh_cmd, ["--help"])

        self.assertEqual(result.exit_code, 0)
        normalized = " ".join(result.output.split())
        self.assertIn("REST GET/HEAD", normalized)
        self.assertIn("Writes and unknown operations", normalized)
        self.assertIn("trusted system locations", normalized)


class TrustedGitHubExecutionTests(unittest.TestCase):
    def test_bare_gh_is_resolved_from_trusted_system_locations(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch.object(
            cmd_mod,
            "trusted_executable",
            return_value="/opt/homebrew/bin/gh",
        ), patch.object(
            cmd_mod.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = cmd_mod._run_command(["gh", "api", "rate_limit"])

        self.assertIs(result, completed)
        run.assert_called_once_with([
            "/opt/homebrew/bin/gh",
            "api",
            "rate_limit",
        ])

    def test_missing_trusted_gh_fails_closed(self):
        with patch.object(cmd_mod, "trusted_executable", return_value=None), \
             patch.object(cmd_mod.subprocess, "run") as run:
            with self.assertRaisesRegex(Exception, "trusted gh executable"):
                cmd_mod._run_command(["gh", "api", "rate_limit"])

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

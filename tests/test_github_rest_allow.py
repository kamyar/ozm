#!/usr/bin/env python3

import shlex
import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import github_api
from ozm.approve import ApprovalResult


META = [
    "--agent-name", "GitHub REST test",
    "--agent-description", "Exercise read-only GitHub REST auto-allow behavior.",
]


class GitHubRESTParserTests(unittest.TestCase):
    def test_implicit_get_with_output_and_pagination_options(self):
        args = [
            "gh", "api", "--paginate",
            "repos/doordash/pedregal/pulls/123/comments",
            "--jq", ".[] | .id",
        ]

        request = github_api.extract_rest_request(args)

        self.assertEqual(
            request,
            github_api.GitHubRESTRequest(
                method="GET",
                endpoint="repos/doordash/pedregal/pulls/123/comments",
            ),
        )
        self.assertEqual(github_api.read_only_reason(args), "github rest GET")

    def test_explicit_head_is_read_only(self):
        args = ["/opt/homebrew/bin/gh", "api", "-XHEAD", "/rate_limit"]

        request = github_api.extract_rest_request(args)

        self.assertEqual(request.method, "HEAD")
        self.assertEqual(github_api.read_only_reason(args), "github rest HEAD")

    def test_explicit_get_can_use_non_file_query_fields(self):
        args = [
            "gh", "api", "repos/doordash/pedregal/issues",
            "--method=GET", "-f", "state=open", "-Fper_page=100",
        ]

        request = github_api.extract_rest_request(args)

        self.assertEqual(request.method, "GET")
        self.assertEqual(github_api.read_only_reason(args), "github rest GET")

    def test_body_fields_default_to_post(self):
        args = [
            "gh", "api", "repos/doordash/pedregal/issues/123/comments",
            "-f", "body=hello",
        ]

        request = github_api.extract_rest_request(args)

        self.assertEqual(request.method, "POST")
        self.assertIsNone(github_api.read_only_reason(args))

    def test_unsafe_or_ambiguous_requests_are_not_auto_allowed(self):
        cases = [
            ["gh", "api", "-X", "POST", "repos/doordash/pedregal/issues"],
            ["gh", "api", "-X", "DELETE", "repos/doordash/pedregal/issues/123"],
            ["gh", "api", "-X", "GET", "-X", "GET", "rate_limit"],
            ["gh", "api", "-X", "GET", "--method", "POST", "rate_limit"],
            ["gh", "api", "-X", "GET", "--input", "request.json", "rate_limit"],
            ["gh", "api", "-X", "GET", "-F", "query=@query.txt", "search/issues"],
            ["gh", "api", "-X", "GET", "-H", "X-HTTP-Method-Override: DELETE", "rate_limit"],
            ["gh", "api", "-X", "GET", "-H", "@headers.txt", "rate_limit"],
            ["gh", "api", "--unknown-option", "rate_limit"],
            ["gh", "api", "https://example.com/collect"],
            ["gh", "api", "rate_limit", "extra-operand"],
            ["gh", "api", "graphql"],
        ]

        for args in cases:
            with self.subTest(args=args):
                self.assertIsNone(github_api.read_only_reason(args))


class GitHubRESTReadAutoAllowTests(unittest.TestCase):
    def run_cmd(self, args, *, blocked=None, approval=None):
        completed = subprocess.CompletedProcess(args=args, returncode=0)
        approval = approval or ApprovalResult(approved=False)
        with patch.object(cmd_mod, "is_command_blocked", return_value=blocked) as is_blocked, \
             patch.object(cmd_mod, "is_command_allowed", return_value=False) as is_allowed, \
             patch.object(cmd_mod, "load_hashes", return_value={}) as load_hashes, \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=approval,
             ) as request_approval, \
             patch.object(
                 cmd_mod,
                 "_run_command",
                 return_value=completed,
             ) as run_command, \
             patch.object(cmd_mod, "audit_log") as audit_log:
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, *args])
        return (
            result,
            is_blocked,
            is_allowed,
            load_hashes,
            request_approval,
            run_command,
            audit_log,
        )

    def test_rest_get_runs_without_approval_or_cache(self):
        args = [
            "gh", "api", "repos/doordash/pedregal/pulls/123/reviews",
            "--paginate", "--jq", ".[] | {id,state}",
        ]

        result, _blocked, is_allowed, load_hashes, request_approval, run_command, audit_log = self.run_cmd(args)

        self.assertEqual(result.exit_code, 0, result.output)
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()
        run_command.assert_called_once_with(args)
        audit_log.assert_called_once_with(
            "semantic",
            "cmd",
            shlex.join(args),
            "github rest GET",
        )
        self.assertIn("allowed (github rest GET)", result.output)

    def test_rest_write_still_requires_approval(self):
        args = [
            "gh", "api", "-X", "POST",
            "repos/doordash/pedregal/pulls/123/comments/456/replies",
            "-f", "body=hello",
        ]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(args)

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_called_once()
        run_command.assert_not_called()
        self.assertIn("denied cmd", result.output)

    def test_blocklist_wins_over_rest_get_auto_allow(self):
        args = ["gh", "api", "repos/doordash/pedregal/pulls/123/reviews"]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(
            args,
            blocked="gh api repos/doordash/pedregal/*",
        )

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_not_called()
        run_command.assert_not_called()
        self.assertIn("blocked by pattern", result.output)


if __name__ == "__main__":
    unittest.main()

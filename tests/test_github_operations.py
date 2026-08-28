#!/usr/bin/env python3

import subprocess
import unittest
from unittest.mock import ANY, patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import config as config_mod
from ozm import gh as gh_mod
from ozm import github_operations
from ozm.approve import ApprovalResult


META = [
    "--agent-name", "Typed GitHub test",
    "--agent-description", "Exercise typed GitHub review reply behavior.",
]


class ReviewReplyParserTests(unittest.TestCase):
    def test_parse_typed_review_reply(self):
        operation = github_operations.parse_review_reply([
            "pr", "review-reply",
            "--repo", "doordash/pedregal",
            "--number", "458415",
            "--comment-id", "3867433262",
            "--body", "fixed",
        ])

        self.assertEqual(operation.repository, "doordash/pedregal")
        self.assertEqual(operation.number, 458415)
        self.assertEqual(operation.comment_id, 3867433262)
        self.assertEqual(operation.body, "fixed")
        self.assertEqual(
            operation.endpoint,
            "repos/doordash/pedregal/pulls/458415/comments/3867433262/replies",
        )

    def test_parse_body_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("reply.md", "w") as f:
                f.write("fixed\n")
            operation = github_operations.parse_review_reply([
                "pr", "review-reply",
                "--repo=doordash/pedregal",
                "--number=458415",
                "--comment-id=3867433262",
                "--body-file=reply.md",
            ])

        self.assertEqual(operation.body_file, "reply.md")
        self.assertIsNone(operation.body)

    def test_invalid_typed_review_reply_fails_closed(self):
        cases = [
            ["pr", "review-reply", "--repo", "invalid", "--number", "1", "--comment-id", "2", "--body", "x"],
            ["pr", "review-reply", "--repo", "o/r", "--number", "0", "--comment-id", "2", "--body", "x"],
            ["pr", "review-reply", "--repo", "o/r", "--number", "1", "--comment-id", "2"],
            ["pr", "review-reply", "--repo", "o/r", "--number", "1", "--comment-id", "2", "--body", "x", "--body-file", "x.md"],
            ["pr", "review-reply", "--repo", "o/r", "--number", "1", "--comment-id", "2", "--body", "x", "--unknown"],
        ]

        for args in cases:
            with self.subTest(args=args):
                with self.assertRaises(Exception):
                    github_operations.parse_review_reply(args)

    def test_match_raw_review_reply_post(self):
        operation = github_operations.match_raw_review_reply([
            "gh", "api", "-X", "POST",
            "repos/doordash/pedregal/pulls/458415/comments/3867433262/replies",
            "-f", "body=fixed",
        ])

        self.assertIsNotNone(operation)
        self.assertEqual(operation.repository, "doordash/pedregal")
        self.assertEqual(operation.body, "fixed")

    def test_match_file_backed_and_implicit_review_reply_posts(self):
        cases = [
            [
                "gh", "api", "-X", "POST",
                "repos/o/r/pulls/1/comments/2/replies",
                "-F", "body=@reply.md",
            ],
            [
                "gh", "api",
                "repos/o/r/pulls/1/comments/2/replies",
                "-f", "body=fixed",
            ],
        ]

        for args in cases:
            with self.subTest(args=args):
                self.assertIsNotNone(
                    github_operations.match_raw_review_reply(args)
                )

    def test_does_not_match_reads_or_other_posts(self):
        cases = [
            ["gh", "api", "repos/o/r/pulls/1/comments/2/replies"],
            ["gh", "api", "-X", "POST", "repos/o/r/issues/1/comments", "-f", "body=x"],
            ["gh", "api", "-X", "PATCH", "repos/o/r/pulls/1/comments/2/replies", "-f", "body=x"],
            ["gh", "api", "-X", "POST", "-X", "POST", "repos/o/r/pulls/1/comments/2/replies", "-f", "body=x"],
        ]

        for args in cases:
            with self.subTest(args=args):
                self.assertIsNone(github_operations.match_raw_review_reply(args))


class AddSubIssueParserTests(unittest.TestCase):
    def test_parse_and_translate_typed_add_sub_issue(self):
        operation = github_operations.parse_add_sub_issue([
            "issue", "add-sub-issue",
            "--repo", "doordash/pedregal",
            "--parent", "450132",
            "--sub-issue-id", "5278154076",
        ])

        self.assertEqual(operation.repository, "doordash/pedregal")
        self.assertEqual(operation.parent, 450132)
        self.assertEqual(operation.sub_issue_id, 5278154076)
        self.assertEqual(
            operation.execution_args(),
            [
                "gh", "api", "--method", "POST",
                "repos/doordash/pedregal/issues/450132/sub_issues",
                "-F", "sub_issue_id=5278154076",
            ],
        )

    def test_invalid_add_sub_issue_fails_closed(self):
        cases = [
            ["issue", "add-sub-issue", "--repo", "bad", "--parent", "1", "--sub-issue-id", "2"],
            ["issue", "add-sub-issue", "--repo", "o/r", "--parent", "0", "--sub-issue-id", "2"],
            ["issue", "add-sub-issue", "--repo", "o/r", "--parent", "1"],
            ["issue", "add-sub-issue", "--repo", "o/r", "--parent", "1", "--sub-issue-id", "2", "--unknown"],
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(Exception):
                github_operations.parse_add_sub_issue(args)

    def test_match_raw_add_sub_issue_post(self):
        operation = github_operations.match_raw_add_sub_issue([
            "gh", "api", "--method", "POST",
            "repos/doordash/pedregal/issues/450132/sub_issues",
            "-F", "sub_issue_id=5278154076",
        ])

        self.assertIsNotNone(operation)
        self.assertEqual(operation.repository, "doordash/pedregal")
        self.assertEqual(operation.parent, 450132)
        self.assertEqual(operation.sub_issue_id, 5278154076)


class GitHubOperationConfigTests(unittest.TestCase):
    def test_operation_authorization_requires_exact_operation_and_repository(self):
        configs = [{
            "github": {
                "allowed_operations": [{
                    "operation": "pr.review-reply",
                    "repositories": ["doordash/pedregal"],
                }],
            },
        }]
        with patch.object(config_mod, "_command_configs", return_value=configs):
            self.assertTrue(
                config_mod.github_operation_allowed(
                    "pr.review-reply", "DoorDash/Pedregal"
                )
            )
            self.assertFalse(
                config_mod.github_operation_allowed(
                    "issue.add-sub-issue", "doordash/pedregal"
                )
            )
            self.assertFalse(
                config_mod.github_operation_allowed(
                    "pr.review-reply", "doordash/other"
                )
            )

    def test_wildcards_and_malformed_entries_do_not_authorize(self):
        configs = [{
            "github": {
                "allowed_operations": [
                    {"operation": "pr.review-reply", "repositories": ["*"]},
                    {"operation": "pr.review-reply", "repositories": "doordash/pedregal"},
                    "pr.review-reply",
                ],
            },
        }]
        with patch.object(config_mod, "_command_configs", return_value=configs):
            self.assertFalse(
                config_mod.github_operation_allowed(
                    "pr.review-reply", "doordash/pedregal"
                )
            )


class ReviewReplyPolicyTests(unittest.TestCase):
    def test_raw_post_through_ozm_gh_is_blocked_with_typed_suggestion(self):
        args = [
            *META,
            "api", "-X", "POST",
            "repos/doordash/pedregal/pulls/458415/comments/3867433262/replies",
            "-f", "body=fixed",
        ]
        with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log") as audit_log:
            result = CliRunner().invoke(gh_mod.gh_cmd, args)

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("raw review-reply POST is not allowed", result.output)
        self.assertIn("pr review-reply", result.output)
        self.assertIn("--repo doordash/pedregal", result.output)
        self.assertIn("--comment-id 3867433262", result.output)
        is_blocked.assert_not_called()
        request_approval.assert_not_called()
        audit_log.assert_called_once()
        self.assertEqual(audit_log.call_args.args[1], "gh")

    def test_typed_review_reply_uses_normal_write_approval(self):
        typed = [
            "pr", "review-reply",
            "--repo", "doordash/pedregal",
            "--number", "458415",
            "--comment-id", "3867433262",
            "--body", "fixed",
        ]
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "github_operation_allowed", return_value=False), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=ApprovalResult(approved=False),
             ) as request_approval, \
             patch.object(cmd_mod, "_run_command") as run_command, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(gh_mod.gh_cmd, [*META, *typed])

        self.assertEqual(result.exit_code, cmd_mod.DENIED)
        request_approval.assert_called_once()
        approved_command = request_approval.call_args.args[0]
        self.assertIn("gh pr review-reply", approved_command)
        self.assertNotIn("api -X POST", approved_command)
        run_command.assert_not_called()

    def test_repository_authorized_review_reply_skips_approval(self):
        typed = [
            "pr", "review-reply",
            "--repo", "doordash/pedregal",
            "--number", "458415",
            "--comment-id", "3867433262",
            "--body", "fixed",
        ]
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "github_operation_allowed", return_value=True), \
             patch.object(cmd_mod, "is_command_allowed") as is_allowed, \
             patch.object(cmd_mod, "load_hashes") as load_hashes, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(
                 cmd_mod,
                 "_run_command",
                 return_value=completed,
             ) as run_command, \
             patch.object(cmd_mod, "audit_log") as audit_log:
            result = CliRunner().invoke(gh_mod.gh_cmd, [*META, *typed])

        self.assertEqual(result.exit_code, 0, result.output)
        request_approval.assert_not_called()
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        run_command.assert_called_once()
        audit_log.assert_called_once_with(
            "operation",
            "gh",
            ANY,
            "github pr.review-reply for doordash/pedregal",
        )

    def test_raw_add_sub_issue_is_blocked_with_typed_suggestion(self):
        args = [
            *META,
            "api", "--method", "POST",
            "repos/doordash/pedregal/issues/450132/sub_issues",
            "-F", "sub_issue_id=5278154076",
        ]
        with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(gh_mod.gh_cmd, args)

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("issue add-sub-issue", result.output)
        self.assertIn("--parent 450132", result.output)
        self.assertIn("--sub-issue-id 5278154076", result.output)
        is_blocked.assert_not_called()
        request_approval.assert_not_called()

    def test_typed_add_sub_issue_translates_to_fixed_endpoint(self):
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
            result = cmd_mod._run_command([
                "gh", "issue", "add-sub-issue",
                "--repo", "doordash/pedregal",
                "--parent", "450132",
                "--sub-issue-id", "5278154076",
            ])

        self.assertIs(result, completed)
        run.assert_called_once_with([
            "/opt/homebrew/bin/gh",
            "api", "--method", "POST",
            "repos/doordash/pedregal/issues/450132/sub_issues",
            "-F", "sub_issue_id=5278154076",
        ])

    def test_typed_execution_translates_to_fixed_rest_endpoint(self):
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
            result = cmd_mod._run_command([
                "gh", "pr", "review-reply",
                "--repo", "doordash/pedregal",
                "--number", "458415",
                "--comment-id", "3867433262",
                "--body", "fixed",
            ])

        self.assertIs(result, completed)
        run.assert_called_once_with([
            "/opt/homebrew/bin/gh",
            "api", "-X", "POST",
            "repos/doordash/pedregal/pulls/458415/comments/3867433262/replies",
            "-f", "body=fixed",
        ])


if __name__ == "__main__":
    unittest.main()

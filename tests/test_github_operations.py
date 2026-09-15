#!/usr/bin/env python3

import json
import os
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
            "--repo", "example/widgets",
            "--number", "42",
            "--comment-id", "9001",
            "--body", "fixed",
        ])

        self.assertEqual(operation.repository, "example/widgets")
        self.assertEqual(operation.number, 42)
        self.assertEqual(operation.comment_id, 9001)
        self.assertEqual(operation.body, "fixed")
        self.assertEqual(
            operation.endpoint,
            "repos/example/widgets/pulls/42/comments/9001/replies",
        )

    def test_parse_body_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("reply.md", "w") as f:
                f.write("fixed\n")
            operation = github_operations.parse_review_reply([
                "pr", "review-reply",
                "--repo=example/widgets",
                "--number=42",
                "--comment-id=9001",
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
            "repos/example/widgets/pulls/42/comments/9001/replies",
            "-f", "body=fixed",
        ])

        self.assertIsNotNone(operation)
        self.assertEqual(operation.repository, "example/widgets")
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
            "--repo", "example/widgets",
            "--parent", "84",
            "--sub-issue-id", "5278154076",
        ])

        self.assertEqual(operation.repository, "example/widgets")
        self.assertEqual(operation.parent, 84)
        self.assertEqual(operation.sub_issue_id, 5278154076)
        self.assertEqual(
            operation.execution_args(),
            [
                "gh", "api", "--method", "POST",
                "repos/example/widgets/issues/84/sub_issues",
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
            "repos/example/widgets/issues/84/sub_issues",
            "-F", "sub_issue_id=5278154076",
        ])

        self.assertIsNotNone(operation)
        self.assertEqual(operation.repository, "example/widgets")
        self.assertEqual(operation.parent, 84)
        self.assertEqual(operation.sub_issue_id, 5278154076)


class CreateIssuesBatchTests(unittest.TestCase):
    def write_batch(self):
        with open("first.md", "w") as file:
            file.write("First body\n")
        with open("second.md", "w") as file:
            file.write("Second body\n")
        manifest = {
            "version": 1,
            "issues": [
                {
                    "title": "First issue",
                    "body_file": "first.md",
                    "labels": ["bug", "priority/medium"],
                },
                {
                    "title": "Second issue",
                    "body_file": "second.md",
                    "labels": [],
                },
            ],
        }
        with open("issues.json", "w") as file:
            json.dump(manifest, file)
        return os.path.abspath("issues.json")

    def test_parser_freezes_body_content_and_builds_review_summary(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            manifest = self.write_batch()
            operation = github_operations.parse_create_issues_batch([
                "issue", "create-batch",
                "--repo", "example/widgets",
                "--manifest", manifest,
            ])
            with open("first.md", "w") as file:
                file.write("Changed after parse\n")

        self.assertEqual(operation.repository, "example/widgets")
        self.assertEqual(len(operation.issues), 2)
        self.assertEqual(operation.issues[0].body, b"First body\n")
        summary = json.loads(operation.review_summary())
        self.assertEqual(summary["issues"][0]["title"], "First issue")
        self.assertEqual(
            summary["issues"][0]["labels"],
            ["bug", "priority/medium"],
        )
        self.assertEqual(len(summary["issues"][0]["body_sha256"]), 64)

    def test_invalid_batch_manifests_fail_closed(self):
        runner = CliRunner()
        bad_payloads = [
            {"version": 1, "issues": []},
            {"version": 2, "issues": []},
            {
                "version": 1,
                "issues": [{
                    "title": "Bad\ntitle",
                    "body_file": "body.md",
                    "labels": [],
                }],
            },
            {
                "version": 1,
                "issues": [{
                    "title": "Missing body",
                    "body_file": "missing.md",
                    "labels": [],
                }],
            },
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), runner.isolated_filesystem():
                with open("body.md", "w") as file:
                    file.write("body\n")
                with open("issues.json", "w") as file:
                    json.dump(payload, file)
                with self.assertRaises(Exception):
                    github_operations.parse_create_issues_batch([
                        "issue", "create-batch",
                        "--repo", "example/widgets",
                        "--manifest", "issues.json",
                    ])

    def test_one_aggregate_approval_executes_frozen_issue_bodies(self):
        runner = CliRunner()
        captured_bodies = []

        def execute(args):
            body_file = args[args.index("--body-file") + 1]
            with open(body_file, "rb") as file:
                captured_bodies.append(file.read())
            return subprocess.CompletedProcess(args, 0)

        with runner.isolated_filesystem():
            manifest = self.write_batch()
            with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
                 patch.object(
                     cmd_mod,
                     "request_approval",
                     return_value=ApprovalResult(approved=True),
                 ) as request_approval, \
                 patch.object(cmd_mod, "request_cmd_approval") as request_cmd, \
                 patch.object(cmd_mod, "github_operation_allowed") as operation_allowed, \
                 patch.object(cmd_mod, "_run_command", side_effect=execute) as run_command, \
                 patch.object(cmd_mod, "audit_log"):
                result = runner.invoke(
                    gh_mod.gh_cmd,
                    [
                        *META,
                        "issue", "create-batch",
                        "--repo", "example/widgets",
                        "--manifest", manifest,
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        request_approval.assert_called_once()
        request_cmd.assert_not_called()
        operation_allowed.assert_not_called()
        self.assertEqual(run_command.call_count, 2)
        self.assertEqual(captured_bodies, [b"First body\n", b"Second body\n"])
        review = json.loads(request_approval.call_args.kwargs["content"])
        self.assertEqual(len(review["issues"]), 2)
        self.assertEqual(review["repository"], "example/widgets")

    def test_batch_stops_and_reports_partial_failure(self):
        runner = CliRunner()
        results = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 7),
        ]
        with runner.isolated_filesystem():
            manifest = self.write_batch()
            with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
                 patch.object(
                     cmd_mod,
                     "request_approval",
                     return_value=ApprovalResult(approved=True),
                 ), patch.object(
                     cmd_mod,
                     "_run_command",
                     side_effect=results,
                 ) as run_command, patch.object(cmd_mod, "audit_log") as audit_log:
                result = runner.invoke(
                    gh_mod.gh_cmd,
                    [
                        *META,
                        "issue", "create-batch",
                        "--repo", "example/widgets",
                        "--manifest", manifest,
                    ],
                )

        self.assertEqual(result.exit_code, 7, result.output)
        self.assertEqual(run_command.call_count, 2)
        self.assertIn("stopped after 1 of 2", result.output)
        self.assertEqual(audit_log.call_args.args[0], "error")

    def test_denied_batch_does_not_create_any_issue(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            manifest = self.write_batch()
            with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
                 patch.object(
                     cmd_mod,
                     "request_approval",
                     return_value=ApprovalResult(approved=False),
                 ) as request_approval, \
                 patch.object(cmd_mod, "_run_command") as run_command, \
                 patch.object(cmd_mod, "audit_log"):
                result = runner.invoke(
                    gh_mod.gh_cmd,
                    [
                        *META,
                        "issue", "create-batch",
                        "--repo", "example/widgets",
                        "--manifest", manifest,
                    ],
                )

        self.assertEqual(result.exit_code, cmd_mod.DENIED, result.output)
        request_approval.assert_called_once()
        run_command.assert_not_called()


class GitHubOperationConfigTests(unittest.TestCase):
    def test_operation_authorization_requires_exact_operation_and_repository(self):
        configs = [{
            "github": {
                "allowed_operations": [{
                    "operation": "pr.review-reply",
                    "repositories": ["example/widgets"],
                }],
            },
        }]
        with patch.object(config_mod, "_command_configs", return_value=configs):
            self.assertTrue(
                config_mod.github_operation_allowed(
                    "pr.review-reply", "Example/Widgets"
                )
            )
            self.assertFalse(
                config_mod.github_operation_allowed(
                    "issue.add-sub-issue", "example/widgets"
                )
            )
            self.assertFalse(
                config_mod.github_operation_allowed(
                    "pr.review-reply", "example/other"
                )
            )

    def test_wildcards_and_malformed_entries_do_not_authorize(self):
        configs = [{
            "github": {
                "allowed_operations": [
                    {"operation": "pr.review-reply", "repositories": ["*"]},
                    {"operation": "pr.review-reply", "repositories": "example/widgets"},
                    "pr.review-reply",
                ],
            },
        }]
        with patch.object(config_mod, "_command_configs", return_value=configs):
            self.assertFalse(
                config_mod.github_operation_allowed(
                    "pr.review-reply", "example/widgets"
                )
            )


class ReviewReplyPolicyTests(unittest.TestCase):
    def test_raw_post_through_ozm_gh_is_blocked_with_typed_suggestion(self):
        args = [
            *META,
            "api", "-X", "POST",
            "repos/example/widgets/pulls/42/comments/9001/replies",
            "-f", "body=fixed",
        ]
        with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log") as audit_log:
            result = CliRunner().invoke(gh_mod.gh_cmd, args)

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("raw review-reply POST is not allowed", result.output)
        self.assertIn("pr review-reply", result.output)
        self.assertIn("--repo example/widgets", result.output)
        self.assertIn("--comment-id 9001", result.output)
        is_blocked.assert_not_called()
        request_approval.assert_not_called()
        audit_log.assert_called_once()
        self.assertEqual(audit_log.call_args.args[1], "gh")

    def test_typed_review_reply_uses_normal_write_approval(self):
        typed = [
            "pr", "review-reply",
            "--repo", "example/widgets",
            "--number", "42",
            "--comment-id", "9001",
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
            "--repo", "example/widgets",
            "--number", "42",
            "--comment-id", "9001",
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
            "github pr.review-reply for example/widgets",
        )

    def test_raw_add_sub_issue_is_blocked_with_typed_suggestion(self):
        args = [
            *META,
            "api", "--method", "POST",
            "repos/example/widgets/issues/84/sub_issues",
            "-F", "sub_issue_id=5278154076",
        ]
        with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(gh_mod.gh_cmd, args)

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("issue add-sub-issue", result.output)
        self.assertIn("--parent 84", result.output)
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
                "--repo", "example/widgets",
                "--parent", "84",
                "--sub-issue-id", "5278154076",
            ])

        self.assertIs(result, completed)
        run.assert_called_once_with([
            "/opt/homebrew/bin/gh",
            "api", "--method", "POST",
            "repos/example/widgets/issues/84/sub_issues",
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
                "--repo", "example/widgets",
                "--number", "42",
                "--comment-id", "9001",
                "--body", "fixed",
            ])

        self.assertIs(result, completed)
        run.assert_called_once_with([
            "/opt/homebrew/bin/gh",
            "api", "-X", "POST",
            "repos/example/widgets/pulls/42/comments/9001/replies",
            "-f", "body=fixed",
        ])


if __name__ == "__main__":
    unittest.main()

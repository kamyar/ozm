#!/usr/bin/env python3
"""Hands-free socket responses must not become persistent permissions."""

import json
import subprocess
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from click.testing import CliRunner

from ozm import app as app_mod
from ozm import approve as approve_mod
from ozm import audit as audit_mod
from ozm import cmd as cmd_mod
from ozm import gh as gh_mod
from ozm import git as git_mod
from ozm import run as run_mod
from ozm.agent import AgentMetadata
from ozm.approve import ApprovalResult
from ozm.github_operations import CreateIssuesBatchOperation, IssueBatchItem

META = [
    "--agent-name", "Hands-free tests",
    "--agent-description", "Test automatic approval behavior.",
]


class HandsFreeResponseTests(unittest.TestCase):
    def test_marker_requires_an_explicit_true_on_an_allow_response(self):
        for marker in (None, False, "true", 1):
            result = approve_mod._parse_socket_response({
                "decision": "allow", "hands_free": marker,
            })
            self.assertFalse(result.hands_free)
            self.assertEqual(result.audit_action("clicked"), "clicked")
        denied = approve_mod._parse_socket_response({
            "decision": "deny", "hands_free": True,
        })
        self.assertFalse(denied.hands_free)
        allowed = approve_mod._parse_socket_response({
            "decision": "allow", "hands_free": True,
        })
        self.assertTrue(allowed.hands_free)
        self.assertEqual(allowed.audit_action("clicked"), "hands-free")
        self.assertEqual(allowed.audit_action("override"), "hands-free")

    def test_each_approval_entry_point_preserves_the_marker(self):
        agent = AgentMetadata("Tests", "Test hands-free responses.")
        with patch.object(approve_mod, "_socket_send", return_value={
            "decision": "allow", "hands_free": True,
        }), patch.object(approve_mod, "_approve_file_macos") as file_dialog, \
             patch.object(approve_mod, "_approve_cmd_macos") as cmd_dialog, \
             patch.object(approve_mod, "_override_macos") as override_dialog:
            results = [
                approve_mod.request_approval("test.sh", "NEW", agent, content="echo ok"),
                approve_mod.request_cmd_approval("example-tool", agent),
                approve_mod.request_override("example-tool", "blocked", "test", agent),
            ]
        self.assertTrue(all(result.hands_free for result in results))
        file_dialog.assert_not_called()
        cmd_dialog.assert_not_called()
        override_dialog.assert_not_called()


class HandsFreeExecutionTests(unittest.TestCase):
    def command_patches(self, stack, *, blocked=None):
        stack.enter_context(patch.object(cmd_mod, "configured_entrypoint_redirect", return_value=None))
        stack.enter_context(patch.object(cmd_mod, "is_command_blocked", return_value=blocked))
        stack.enter_context(patch.object(cmd_mod, "is_command_allowed", return_value=False))
        stack.enter_context(patch.object(cmd_mod, "load_hashes", return_value={}))
        stack.enter_context(patch.object(approve_mod, "_socket_send", return_value={
            "decision": "allow", "hands_free": True,
        }))
        save = stack.enter_context(patch.object(cmd_mod, "save_hashes"))
        allow = stack.enter_context(patch.object(cmd_mod, "add_allowed_command"))
        run = stack.enter_context(patch.object(cmd_mod, "_run_command", return_value=
            subprocess.CompletedProcess([], 7)))
        audit = stack.enter_context(patch.object(cmd_mod, "audit_log"))
        return save, allow, run, audit

    def test_commands_and_github_writes_execute_without_saving_permissions(self):
        for command, args in (
            (cmd_mod.cmd_cmd, ["example-tool", "update"]),
            (gh_mod.gh_cmd, ["pr", "comment", "42", "--body", "test"]),
        ):
            with self.subTest(command=command.name), ExitStack() as stack:
                save, allow, run, audit = self.command_patches(stack)
                result = CliRunner().invoke(command, [*META, *args])
            self.assertEqual(result.exit_code, 7, result.output)
            save.assert_not_called()
            allow.assert_not_called()
            run.assert_called_once()
            self.assertEqual(audit.call_args.args[0], "hands-free")

    def test_blocklist_still_requires_an_override_reason(self):
        with ExitStack() as stack:
            save, allow, run, audit = self.command_patches(stack, blocked="example-tool *")
            with patch.object(cmd_mod, "request_override") as override:
                result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "example-tool", "update"])
        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        override.assert_not_called()
        run.assert_not_called()
        save.assert_not_called()

    def test_reasoned_override_is_automatic_and_not_cached(self):
        with ExitStack() as stack:
            save, allow, run, audit = self.command_patches(stack, blocked="example-tool *")
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [
                *META, "--reason", "Test the override.", "example-tool", "update",
            ])
        self.assertEqual(result.exit_code, 7, result.output)
        run.assert_called_once()
        save.assert_not_called()
        allow.assert_not_called()
        self.assertEqual(audit.call_args.args[0], "hands-free")

    def test_hard_blocks_still_precede_approval(self):
        with ExitStack() as stack:
            _, _, run, _ = self.command_patches(stack)
            with patch.object(cmd_mod, "request_cmd_approval") as approval:
                result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "sed", "s/a/b/", "file"])
        self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
        approval.assert_not_called()
        run.assert_not_called()

    def test_scripts_execute_without_saving_hashes_or_snapshots(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("script.sh", "w") as file:
                file.write("#!/bin/sh\necho first\necho second\n")
            with patch.object(run_mod, "load_entrypoint_redirects", return_value=[]), \
                 patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(approve_mod, "_socket_send", return_value={
                     "decision": "allow", "hands_free": True,
                 }), \
                 patch.object(run_mod, "save_hashes") as save, \
                 patch.object(run_mod, "save_snapshot") as snapshot, \
                 patch.object(run_mod, "_execute_script", side_effect=SystemExit(0)) as execute, \
                 patch.object(run_mod, "audit_log") as audit:
                result = runner.invoke(run_mod.run_cmd, [*META, "script.sh"])
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_called_once()
        save.assert_not_called()
        snapshot.assert_not_called()
        self.assertEqual(audit.call_args.args[0], "hands-free")

    def test_batch_approval_is_recorded_before_execution_even_on_partial_failure(self):
        operation = CreateIssuesBatchOperation(
            repository="example/widgets",
            manifest="issues.json",
            issues=tuple(
                IssueBatchItem(title, (), "body.md", b"Test body\n")
                for title in ("First issue", "Second issue")
            ),
        )
        agent = AgentMetadata("Tests", "Test GitHub batch audit records.")
        for hands_free in (False, True):
            for exit_codes in ([7], [0, 7], [0, 0]):
                with self.subTest(hands_free=hands_free, exit_codes=exit_codes):
                    events = []
                    remaining = iter(exit_codes)

                    def execute(args):
                        events.append("execute")
                        return subprocess.CompletedProcess(args, next(remaining))

                    with patch.object(
                        cmd_mod, "request_approval",
                        return_value=ApprovalResult(True, hands_free=hands_free),
                    ), patch.object(cmd_mod, "_run_command", side_effect=execute), \
                         patch.object(
                             cmd_mod, "audit_log",
                             side_effect=lambda action, *_args: events.append(action),
                         ) as audit:
                        with self.assertRaises(SystemExit) as stopped:
                            cmd_mod._review_and_execute_issue_batch(operation, agent, "gh issue create-batch")
                    self.assertEqual(stopped.exception.code, exit_codes[-1])
                    action = "hands-free" if hands_free else "clicked"
                    expected = [action, *(["execute"] * len(exit_codes))]
                    if exit_codes[-1]:
                        expected.append("error")
                    self.assertEqual(events, expected)
                    self.assertEqual(audit.call_args_list[0].args[3],
                                     "approved 2 issue creations for example/widgets")
                    summary = audit_mod._summary([
                        {"action": call.args[0], "kind": "gh", "target": "gh issue create-batch"}
                        for call in audit.call_args_list
                    ])
                    self.assertEqual(summary["hands_free_approvals"], int(hands_free))
                    self.assertEqual(summary["manual_approvals"], int(not hands_free))

    def test_git_overrides_use_the_automatic_audit_action(self):
        with patch.object(approve_mod, "_socket_send", return_value={
            "decision": "allow", "hands_free": True,
        }), patch.object(git_mod, "audit_log") as audit:
            git_mod._handle_violation(
                "protected branch", "git push origin main", "Test the override.",
                AgentMetadata("Tests", "Test a Git override."),
            )
        audit.assert_called_once_with("hands-free", "git", "git push origin main", None)


class HandsFreeDiagnosticsTests(unittest.TestCase):
    def test_app_status_shows_the_mode_and_supports_older_apps(self):
        for info, expected in (({"hands_free": True}, "on"), ({}, "off")):
            with patch.object(app_mod.os.path, "exists", return_value=True), \
                 patch.object(app_mod, "send_request", return_value={"feedback": json.dumps(info)}):
                result = CliRunner().invoke(app_mod.app_cmd, ["status"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn(f"hands-free: {expected}", result.output)

    def test_audit_does_not_count_automatic_approvals_as_manual(self):
        summary = audit_mod._summary([
            {"action": "hands-free", "kind": "cmd", "target": "example-tool"},
            {"action": "hands-free", "kind": "run", "target": "script.sh"},
            {"action": "hands-free", "kind": "git", "target": "git push origin main"},
            {"action": "clicked", "kind": "cmd", "target": "other-tool"},
        ])
        self.assertEqual(summary["hands_free_approvals"], 3)
        self.assertEqual(summary["manual_approvals"], 1)
        self.assertEqual(audit_mod._REASONS["hands-free"], "automatic approval in hands-free mode")

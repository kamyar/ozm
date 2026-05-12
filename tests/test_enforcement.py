import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import git as git_mod
from ozm import install as install_mod
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Unit test",
    "--agent-description", "Exercise ozm command behavior.",
]


class CmdTests(unittest.TestCase):
    def test_cmd_rejects_git_passthrough(self):
        result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "git", "status"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("use 'ozm git --agent-name", result.output)

    def test_cmd_rejects_sed_with_alternatives(self):
        with patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
            patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "sed", "-n", "1p", "README.md"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("blocked command 'sed'", result.output)
        self.assertIn("rg for searching", result.output)
        self.assertIn("cat/nl/head/tail for viewing", result.output)
        request_cmd_approval.assert_not_called()

    def test_cmd_rejects_path_sed(self):
        with patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "/usr/bin/sed", "-n", "1p", "README.md"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("blocked command 'sed'", result.output)

    def test_cmd_rejects_env_prefixed_sed(self):
        with patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "env", "LC_ALL=C", "sed", "-n", "1p", "README.md"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("blocked command 'sed'", result.output)

    def test_blocked_override_executes_once_without_second_approval(self):
        completed = subprocess.CompletedProcess(args="rm -rf build", returncode=0)

        with patch.object(cmd_mod, "is_command_blocked", return_value="rm -rf *"), \
            patch.object(cmd_mod, "request_override", return_value=ApprovalResult(True)), \
            patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
            patch.object(cmd_mod, "subprocess") as subprocess_mod, \
            patch.object(cmd_mod, "audit_log"):
            subprocess_mod.run.return_value = completed

            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "rm", "-rf", "build", "--reason", "clean generated files"],
            )

        self.assertEqual(result.exit_code, 0)
        subprocess_mod.run.assert_called_once_with(["rm", "-rf", "build"])
        request_cmd_approval.assert_not_called()


class GitTests(unittest.TestCase):
    def test_push_blocks_force_with_lease(self):
        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"):
            self.assertEqual(
                git_mod._check_push(["--force-with-lease"]),
                "force push is not allowed",
            )

    def test_push_blocks_plus_prefixed_protected_branch(self):
        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"):
            self.assertEqual(
                git_mod._check_push(["origin", "+main"]),
                "pushing to 'main' is not allowed",
            )

    def test_push_blocks_plus_prefixed_ref(self):
        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"):
            self.assertEqual(
                git_mod._check_push(["origin", "+refs/heads/master"]),
                "pushing to 'master' is not allowed",
            )


class InstallHookTests(unittest.TestCase):
    def run_hook(self, command):
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(
            [sys.executable, "-c", install_mod.HOOK_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
        )

    def test_hook_blocks_safe_command_with_substitution(self):
        result = self.run_hook("echo $(git status)")

        self.assertEqual(result.returncode, 0)
        self.assertIn("permissionDecision", result.stdout)
        self.assertIn("deny", result.stdout)

    def test_hook_blocks_pipe_segment(self):
        result = self.run_hook("echo ok | git status")

        self.assertEqual(result.returncode, 0)
        self.assertIn("deny", result.stdout)

    def test_hook_allows_quoted_separator_inside_ozm_command(self):
        result = self.run_hook(
            'ozm cmd --agent-name "Unit test" '
            '--agent-description "Exercise hook metadata." '
            'python3 -c "print(1); print(2)"'
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_configure_codex_writes_hook_and_rules(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            config = os.path.abspath("codex/config.toml")
            rules = os.path.abspath("codex/rules/ozm-enforcement.rules")
            hook = os.path.abspath("ozm/hooks/enforce.sh")

            with patch.object(install_mod, "CODEX_CONFIG", config), \
                patch.object(install_mod, "CODEX_RULES", rules), \
                patch.object(
                    install_mod,
                    "CODEX_RULES_DIR",
                    os.path.dirname(rules),
                ), \
                patch.object(install_mod, "ENFORCE_HOOK", hook):
                install_mod._configure_codex()

            with open(config) as f:
                config_text = f.read()
            with open(rules) as f:
                rules_text = f.read()

        self.assertIn("codex_hooks = true", config_text)
        self.assertIn(hook, config_text)
        self.assertIn('decision = "forbidden"', rules_text)

    def test_hook_blocks_sed_with_alternatives(self):
        result = self.run_hook("sed -n '1p' README.md")

        self.assertEqual(result.returncode, 0)
        self.assertIn("deny", result.stdout)
        self.assertIn("sed is disallowed", result.stdout)
        self.assertIn("rg for searching", result.stdout)


if __name__ == "__main__":
    unittest.main()

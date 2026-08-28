import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import cli as cli_mod
from ozm import git as git_mod
from ozm import install as install_mod
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Unit test",
    "--agent-description", "Exercise ozm command behavior.",
]


class TipsTests(unittest.TestCase):
    def test_tips_command_lists_guidance(self):
        result = CliRunner().invoke(cli_mod.tips_cmd, [])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("ozm run <script>", result.output)
        self.assertIn("read-only", result.output)
        self.assertIn("bash -lc", result.output)


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

    def test_cmd_rejects_curl_with_alternatives(self):
        with patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
            patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "curl", "https://example.com"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("blocked command 'curl'", result.output)
        self.assertIn("uv tool install httpie", result.output)
        self.assertIn("httpx", result.output)
        request_cmd_approval.assert_not_called()

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

    def test_cmd_rejects_python_c_inline_code(self):
        result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "python", "-c", "print(1)"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("write the code to a script file", result.output)
        self.assertIn("#!/usr/bin/env python", result.output)

    def test_cmd_rejects_python3_c_inline_code(self):
        result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "python3", "-c", "print(1)"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("write the code to a script file", result.output)
        self.assertIn("#!/usr/bin/env python3", result.output)

    def test_cmd_rejects_uv_run_python_c(self):
        result = CliRunner().invoke(
            cmd_mod.cmd_cmd, [*META, "uv", "run", "python", "-c", "print(1)"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("write the code to a script file", result.output)

    def test_cmd_redirects_direct_script_with_arguments_before_policy(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("poll.sh", "w") as f:
                f.write("#!/usr/bin/env bash\nprintf done\n")
            with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
                 patch.object(cmd_mod, "is_command_allowed") as is_allowed, \
                 patch.object(cmd_mod, "load_hashes") as load_hashes, \
                 patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
                 patch.object(cmd_mod, "audit_log") as audit_log:
                result = runner.invoke(
                    cmd_mod.cmd_cmd,
                    [*META, "poll.sh", "--pr", "123"],
                )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("ozm run", result.output)
        self.assertIn("poll.sh --pr 123", result.output)
        is_blocked.assert_not_called()
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()
        audit_log.assert_called_once_with(
            "blocked",
            "cmd",
            "poll.sh --pr 123",
            "script content must use ozm run",
        )

    def test_script_detection_supports_extensionless_shebang(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("poll-review", "w") as f:
                f.write("#!/usr/bin/env bash\nprintf done\n")

            match = cmd_mod._find_script_in_args(("poll-review", "--quick"))

        self.assertEqual(
            match,
            ("poll-review", "#!/usr/bin/env bash", ("--quick",)),
        )

    def test_script_detection_ignores_python_modules_and_binary_files(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("tool", "wb") as f:
                f.write(b"\x00binary")

            self.assertIsNone(
                cmd_mod._find_script_in_args(("python3", "-m", "pytest"))
            )
            self.assertIsNone(cmd_mod._find_script_in_args(("tool",)))

    def test_cmd_rejects_uv_run_py_script(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("script.py", "w") as f:
                f.write("print('hello')\n")
            with patch.object(cmd_mod, "audit_log"):
                result = runner.invoke(
                    cmd_mod.cmd_cmd,
                    [*META, "uv", "run", "script.py", "--quick"],
                )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("ozm run", result.output)
        self.assertIn("#!/usr/bin/env python3", result.output)
        self.assertIn("script.py --quick", result.output)


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

    def test_hook_accepts_global_grep_with_agent_metadata(self):
        result = self.run_hook(
            'ozm --cwd /tmp --grep "needle" --tail 20 git --agent-name "Search history" '
            '--agent-description "Find a term in historical output." show HEAD:file'
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_hook_checks_metadata_after_global_grep(self):
        result = self.run_hook('ozm --head 20 git show HEAD:file')

        self.assertEqual(result.returncode, 0)
        self.assertIn("deny", result.stdout)
        self.assertIn("requires --agent-name", result.stdout)

    def test_hook_blocks_sed_with_alternatives(self):
        result = self.run_hook("sed -n '1p' README.md")

        self.assertEqual(result.returncode, 0)
        self.assertIn("deny", result.stdout)
        self.assertIn("sed is disallowed", result.stdout)
        self.assertIn("rg for searching", result.stdout)

    def test_hook_blocks_curl_with_alternatives(self):
        result = self.run_hook("curl https://example.com")

        self.assertEqual(result.returncode, 0)
        self.assertIn("deny", result.stdout)
        self.assertIn("curl is disallowed", result.stdout)
        self.assertIn("uv tool install httpie", result.stdout)


if __name__ == "__main__":
    unittest.main()

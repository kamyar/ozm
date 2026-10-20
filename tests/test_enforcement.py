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
        self.assertIn("result file path", result.output)
        self.assertIn("separate call", result.output)

    def test_generated_instructions_explain_result_file_reads(self):
        for instructions in (install_mod.CLAUDE_MD, install_mod.AGENTS_MD):
            self.assertIn("Read returned result files separately", instructions)
            self.assertIn("Do not reuse an earlier result path", instructions)


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

    def test_cmd_blocks_bare_env_before_policy(self):
        with patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
             patch.object(cmd_mod, "is_command_allowed") as is_allowed, \
             patch.object(cmd_mod, "load_hashes") as load_hashes, \
             patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "env"])

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("expose credentials and tokens", result.output)
        is_blocked.assert_not_called()
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()

    def test_env_with_a_child_command_is_not_a_bare_dump(self):
        self.assertFalse(cmd_mod._bare_env_dump(["env", "LC_ALL=C", "printf", "ok"]))
        self.assertTrue(cmd_mod._bare_env_dump(["env", "-u", "TOKEN"]))
        self.assertFalse(cmd_mod._bare_env_dump(["env", "--help"]))

    def test_cmd_blocks_temporary_executable_before_config_or_cache(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            path = os.path.abspath("temporary-binary")
            with open(path, "wb") as f:
                f.write(b"binary content")
            os.chmod(path, 0o700)
            with patch.object(cmd_mod.tempfile, "gettempdir", return_value=os.getcwd()), \
                 patch.object(cmd_mod, "is_command_blocked") as is_blocked, \
                 patch.object(cmd_mod, "is_command_allowed") as is_allowed, \
                 patch.object(cmd_mod, "load_hashes") as load_hashes, \
                 patch.object(cmd_mod, "request_cmd_approval") as request_approval, \
                 patch.object(cmd_mod, "audit_log"):
                result = runner.invoke(cmd_mod.cmd_cmd, [*META, path])

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("temporary executable", result.output)
        self.assertIn("sha256:", result.output)
        is_blocked.assert_not_called()
        is_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_approval.assert_not_called()

    def test_temporary_executable_override_runs_once(self):
        path = "/tmp/ozm-test-temporary-binary"
        temporary = (path, "a" * 64)
        completed = subprocess.CompletedProcess([path], 0)
        with patch.object(
                 cmd_mod,
                 "_temporary_executable",
                 side_effect=[temporary, temporary],
             ), patch.object(
                 cmd_mod,
                 "request_override",
                 return_value=ApprovalResult(approved=True),
             ) as request_override, patch.object(
                 cmd_mod,
                 "_run_command",
                 return_value=completed,
             ) as run_command, patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, path, "--reason", "Pinned checksum from vendor release."],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        request_override.assert_called_once()
        run_command.assert_called_once_with([path])

    def test_changed_temporary_executable_does_not_run(self):
        path = "/tmp/ozm-test-changing-binary"
        with patch.object(
                 cmd_mod,
                 "_temporary_executable",
                 side_effect=[(path, "a" * 64), (path, "b" * 64)],
             ), patch.object(
                 cmd_mod,
                 "request_override",
                 return_value=ApprovalResult(approved=True),
             ), patch.object(cmd_mod, "_run_command") as run_command, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, path, "--reason", "Pinned checksum from vendor release."],
            )

        self.assertEqual(result.exit_code, cmd_mod.BLOCKED)
        self.assertIn("changed after approval", result.output)
        run_command.assert_not_called()

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

    def test_uv_options_do_not_hide_script_content(self):
        runner = CliRunner()
        cases = [
            ("uv", "run", "--no-project", "--with", "httpx", "--with", "rich"),
            ("uv", "run", "--with=httpx", "--python", "3.12", "--"),
            ("uv", "run", "--project", "/work", "--group", "dev"),
            ("/opt/homebrew/bin/uv", "run", "--directory=/work", "--quiet"),
            ("env", "MODE=test", "uv", "run", "--with", "httpx"),
        ]
        with runner.isolated_filesystem():
            with open("script.py", "w") as file:
                file.write("#!/usr/bin/env python3\nprint('one')\nprint('two')\n")
            for prefix in cases:
                with self.subTest(prefix=prefix), \
                     patch.object(cmd_mod, "is_command_blocked") as blocked, \
                     patch.object(cmd_mod, "is_command_allowed") as allowed, \
                     patch.object(cmd_mod, "load_hashes") as hashes, \
                     patch.object(cmd_mod, "request_cmd_approval") as approval, \
                     patch.object(cmd_mod, "_run_command") as execute, \
                     patch.object(cmd_mod, "audit_log"):
                    result = runner.invoke(
                        cmd_mod.cmd_cmd,
                        [*META, *prefix, "script.py", "https://metrics.example/?a=1&b=2"],
                    )
                self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
                self.assertIn("ozm run", result.output)
                self.assertIn("script.py 'https://metrics.example/?a=1&b=2'", result.output)
                blocked.assert_not_called()
                allowed.assert_not_called()
                hashes.assert_not_called()
                approval.assert_not_called()
                execute.assert_not_called()

    def test_local_wrapper_scripts_are_reviewed_before_unwrapping(self):
        runner = CliRunner()
        cases = [
            (("./uv", "run", "echo", "ok"), "./uv", ("run", "echo", "ok")),
            (("./env", "echo", "ok"), "./env", ("echo", "ok")),
            (("/usr/bin/env", "MODE=read", "./uv", "run", "echo", "ok"), "./uv", ("run", "echo", "ok")),
            (("/opt/homebrew/bin/uv", "run", "./env", "echo", "ok"), "./env", ("echo", "ok")),
            (("/opt/homebrew/bin/uv", "run", "./uv", "run", "echo", "ok"), "./uv", ("run", "echo", "ok")),
        ]
        with runner.isolated_filesystem():
            for name in ("uv", "env"):
                with open(name, "w") as file:
                    file.write("#!/usr/bin/env sh\necho one\necho two\n")
                os.chmod(name, 0o700)
            for args, script, script_args in cases:
                with self.subTest(args=args), \
                     patch.object(cmd_mod, "is_command_allowed") as allowed, \
                     patch.object(cmd_mod, "load_hashes") as hashes, \
                     patch.object(cmd_mod, "request_cmd_approval") as approval, \
                     patch.object(cmd_mod, "_run_command") as execute, \
                     patch.object(cmd_mod, "audit_log"):
                    result = runner.invoke(cmd_mod.cmd_cmd, [*META, *args])
                self.assertEqual(result.exit_code, cmd_mod.BLOCKED, result.output)
                self.assertIn("ozm run", result.output)
                self.assertEqual(
                    cmd_mod._find_script_in_args(args),
                    (script, "#!/usr/bin/env sh", script_args),
                )
                allowed.assert_not_called()
                hashes.assert_not_called()
                approval.assert_not_called()
                execute.assert_not_called()

    def test_bare_wrapper_names_do_not_inspect_unrelated_cwd_files(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir("bin")
            bin_path = os.path.abspath("bin")
            for name in ("uv", "env"):
                with open(name, "w") as file:
                    file.write("#!/usr/bin/env sh\necho unrelated\n")
                with open(os.path.join(bin_path, name), "wb") as file:
                    file.write(b"\x7fELF-native-test-fixture")
                os.chmod(os.path.join(bin_path, name), 0o700)
            with patch.dict(os.environ, {"PATH": bin_path}):
                for args in (
                    ("uv", "run", "echo", "ok"),
                    ("uv", "help"),
                    ("env", "uv", "run", "echo", "ok"),
                ):
                    with self.subTest(args=args):
                        self.assertIsNone(cmd_mod._find_script_in_args(args))
                for name in ("uv", "env"):
                    self.assertEqual(
                        cmd_mod._find_script_in_args((f"./{name}", "--quick")),
                        (f"./{name}", "#!/usr/bin/env sh", ("--quick",)),
                    )

    def test_installed_wrapper_names_still_check_the_child_script(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir("bin")
            bin_path = os.path.abspath("bin")
            for name in ("uv", "env"):
                with open(name, "w") as file:
                    file.write("#!/usr/bin/env sh\necho unrelated\n")
                path = os.path.join(bin_path, name)
                with open(path, "w") as file:
                    file.write("#!/usr/bin/env sh\nexec installed-tool \"$@\"\n")
                os.chmod(path, 0o700)
            with open("script.py", "w") as file:
                file.write("#!/usr/bin/env python3\nprint('child script')\n")
            with patch.dict(os.environ, {"PATH": bin_path}):
                for prefix in (("uv", "run"), ("env", "MODE=read", "uv", "run")):
                    with self.subTest(prefix=prefix):
                        self.assertEqual(
                            cmd_mod._find_script_in_args((*prefix, "script.py", "--quick")),
                            ("script.py", "#!/usr/bin/env python3", ("--quick",)),
                        )

    def test_deep_uv_wrappers_do_not_hide_script_or_inline_code(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("script.py", "w") as file:
                file.write("#!/usr/bin/env python3\nprint('one')\nprint('two')\n")
            for depth in (8, 9, 100):
                prefix = ("uv", "run") * depth
                with self.subTest(depth=depth):
                    self.assertEqual(
                        cmd_mod._find_script_in_args((*prefix, "script.py", "--quick")),
                        ("script.py", "#!/usr/bin/env python3", ("--quick",)),
                    )
                    self.assertEqual(
                        cmd_mod._detect_inline_code((*prefix, "python3", "-c", "print(1)")),
                        "python3",
                    )

    def test_uv_options_do_not_hide_inline_code(self):
        with patch.object(cmd_mod, "request_cmd_approval") as approval, \
             patch.object(cmd_mod, "_run_command") as execute:
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "uv", "run", "--with", "httpx", "python3", "-c", "print(1)"],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("write the code to a script file", result.output)
        approval.assert_not_called()
        execute.assert_not_called()

    def test_uv_detection_stops_at_the_child_command(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("script.py", "w") as file:
                file.write("print('hello')\n")
            for args in (
                ("uv", "run", "--with", "httpx", "echo", "script.py"),
                ("uv", "run", "--", "echo", "python3", "-c", "print(1)"),
                ("uv", "run", "--with", "script.py", "pytest", "script.py"),
                ("uv", "run", "--with"),
            ):
                with self.subTest(args=args):
                    self.assertIsNone(cmd_mod._find_script_in_args(args))
                    self.assertIsNone(cmd_mod._detect_inline_code(args))

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
                "force push is not allowed",
            )

    def test_push_blocks_plus_prefixed_ref(self):
        with patch.object(git_mod, "get_current_branch", return_value="kamyar/topic"):
            self.assertEqual(
                git_mod._check_push(["origin", "+refs/heads/master"]),
                "force push is not allowed",
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

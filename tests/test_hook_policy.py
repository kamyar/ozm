import json
import subprocess
import sys
import unittest

from ozm import install as install_mod


class HookPolicyTests(unittest.TestCase):
    def run_hook(self, command, *, key="command"):
        payload = json.dumps({"tool_input": {key: command}})
        result = subprocess.run(
            [sys.executable, "-c", install_mod.HOOK_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        if not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        self.assertEqual(set(data.keys()), {"hookSpecificOutput"})
        return data["hookSpecificOutput"]

    def assert_allowed(self, command, *, key="command"):
        decision = self.run_hook(command, key=key)

        self.assertIsNone(decision)

    def assert_denied(self, command, *reason_fragments, key="command"):
        decision = self.run_hook(command, key=key)

        self.assertIsNotNone(decision)
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        reason = decision["permissionDecisionReason"]
        self.assertTrue(reason)
        for fragment in reason_fragments:
            self.assertIn(fragment, reason)

    def test_direct_git_is_denied_with_ozm_git_guidance(self):
        for command in ("git status", "/usr/bin/git status", "env GIT_DIR=.git git status"):
            with self.subTest(command=command):
                self.assert_denied(
                    command,
                    "Use 'ozm git",
                    "instead of 'git' directly",
                )

    def test_ozm_commands_without_agent_metadata_are_denied(self):
        for command in ("ozm run script.sh", "ozm cmd echo hello", "ozm git status"):
            with self.subTest(command=command):
                self.assert_denied(
                    command,
                    "ozm run/cmd/git requires --agent-name and --agent-description",
                )

    def test_ozm_git_with_metadata_is_allowed(self):
        self.assert_allowed(
            'ozm git --agent-name "Inspect repo" '
            '--agent-description "Check git status." status'
        )

    def test_cmd_payload_key_is_supported(self):
        self.assert_allowed(
            'ozm cmd --agent-name "Print date" '
            '--agent-description "Run a safe command." date',
            key="cmd",
        )

    def test_ozm_cmd_with_top_level_redirection_is_denied(self):
        self.assert_denied(
            'ozm cmd --agent-name "Leak file" '
            '--agent-description "Try to redirect output." '
            "echo secret > /tmp/leak",
            "Top-level redirection",
        )

    def test_ozm_run_with_top_level_redirection_is_denied(self):
        self.assert_denied(
            'ozm run --agent-name "Run script" '
            '--agent-description "Execute reviewed script." '
            "script.sh > /tmp/out",
            "Top-level redirection",
        )

    def test_path_prefixed_sed_is_denied_with_alternatives(self):
        self.assert_denied(
            "/usr/bin/sed -n '1p' README.md",
            "sed is disallowed",
            "rg for searching",
        )

    def test_env_prefixed_sed_is_denied(self):
        self.assert_denied(
            "env LC_ALL=C sed -n '1p' README.md",
            "sed is disallowed",
            "rg for searching",
        )


if __name__ == "__main__":
    unittest.main()

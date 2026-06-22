import json
import os
import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import audit as audit_mod
from ozm import cli as cli_mod
from ozm import cmd as cmd_mod
from ozm import config as config_mod
from ozm import doctor as doctor_mod
from ozm import run as run_mod
from ozm import shell as shell_mod
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Pi integration tests",
    "--agent-description", "Exercise pi-facing ozm APIs.",
]


class MetadataApiTests(unittest.TestCase):
    def test_env_metadata_is_accepted(self):
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=ApprovalResult(False),
             ) as request_cmd_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                ["rg", "needle"],
                env={
                    "OZM_AGENT_NAME": "pi",
                    "OZM_AGENT_DESCRIPTION": "Run command through pi.",
                },
            )

        self.assertNotEqual(result.exit_code, 0)
        agent = request_cmd_approval.call_args.args[1]
        self.assertEqual(agent.name, "pi")
        self.assertEqual(agent.description, "Run command through pi.")

    def test_agent_json_is_accepted(self):
        metadata = json.dumps({"name": "json agent", "description": "JSON supplied metadata."})
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=ApprovalResult(False),
             ) as request_cmd_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, ["--agent-json", metadata, "rg", "needle"])

        self.assertNotEqual(result.exit_code, 0)
        agent = request_cmd_approval.call_args.args[1]
        self.assertEqual(agent.name, "json agent")
        self.assertEqual(agent.description, "JSON supplied metadata.")

    def test_safe_readonly_semantics_are_env_gated_for_integrations(self):
        completed = subprocess.CompletedProcess(args=["echo", "ok"], returncode=0)
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed") as is_command_allowed, \
             patch.object(cmd_mod, "load_hashes") as load_hashes, \
             patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
             patch.object(cmd_mod, "_run_command", return_value=completed) as run_command, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(
                cmd_mod.cmd_cmd,
                [*META, "echo", "ok"],
                env={"OZM_SAFE_READONLY": "1"},
            )

        self.assertEqual(result.exit_code, 0)
        run_command.assert_called_once_with(["echo", "ok"])
        is_command_allowed.assert_not_called()
        load_hashes.assert_not_called()
        request_cmd_approval.assert_not_called()


class JsonCliTests(unittest.TestCase):
    def test_config_and_trust_check_json_report_trust_state(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open(".ozm.yaml", "w") as f:
                f.write("allowed_commands:\n  - pytest\n")
            ozm_dir = os.path.abspath("trusted")
            projects_dir = os.path.join(ozm_dir, "projects")
            global_config = os.path.join(ozm_dir, "config.yaml")
            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                 patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                 patch.object(config_mod, "GLOBAL_CONFIG", global_config):
                trust = runner.invoke(cli_mod.trust_cmd, ["--check", "--json"])
                config = runner.invoke(cli_mod.config_cmd, ["--json"])

        self.assertEqual(trust.exit_code, 0, trust.output)
        self.assertEqual(config.exit_code, 0, config.output)
        trust_payload = json.loads(trust.output)
        config_payload = json.loads(config.output)
        self.assertTrue(trust_payload["repo_config_exists"])
        self.assertFalse(trust_payload["trusted_config_exists"])
        self.assertIsNone(trust_payload["trusted_config_differs"])
        self.assertEqual(config_payload["status"], "not_found")

    def test_status_json_lists_cached_command_entries(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            key = run_mod.project_key("cmd:date")
            with patch.object(run_mod, "load_hashes", return_value={key: "hash"}):
                result = runner.invoke(run_mod.status_cmd, ["--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["entries"][0]["kind"], "cmd")
        self.assertEqual(payload["entries"][0]["status"], "ok")
        self.assertEqual(payload["entries"][0]["target"], "cmd:date")

    def test_doctor_json_uses_structured_check_results(self):
        with patch.object(
            doctor_mod,
            "_doctor_results",
            return_value=([{"name": "ozm binary", "ok": True, "status": "ok", "message": "ok"}], True),
        ):
            result = CliRunner().invoke(doctor_mod.doctor_cmd, ["--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["all_ok"])
        self.assertEqual(payload["checks"][0]["name"], "ozm binary")

    def test_audit_log_json_parses_recent_entries(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            audit_file = os.path.join(ozm_dir, "audit.log")
            with patch.object(audit_mod, "OZM_DIR", ozm_dir), \
                 patch.object(audit_mod, "AUDIT_FILE", audit_file):
                audit_mod.log("clicked", "cmd", "echo ok", "approved")
                result = runner.invoke(audit_mod.log_cmd, ["--json", "-n", "1"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        entry = payload["entries"][0]
        self.assertEqual(entry["action"], "clicked")
        self.assertEqual(entry["kind"], "cmd")
        self.assertEqual(entry["target"], "echo ok")
        self.assertEqual(entry["feedback"], "approved")


class StdinShellTests(unittest.TestCase):
    def test_run_stdin_reviews_content_with_stable_title(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        saved_hashes = {}

        def fake_save_hashes(hashes):
            saved_hashes.update(hashes)

        with patch.object(run_mod, "load_hashes", return_value={}), \
             patch.object(run_mod, "save_hashes", side_effect=fake_save_hashes), \
             patch.object(run_mod, "save_snapshot"), \
             patch.object(run_mod, "request_approval", return_value=ApprovalResult(True)) as request_approval, \
             patch.object(run_mod.subprocess, "run", return_value=completed) as subprocess_run, \
             patch.object(run_mod, "audit_log"):
            result = CliRunner().invoke(
                run_mod.run_cmd,
                ["--stdin", "--title", "pi-test", *META],
                input="#!/usr/bin/env sh\necho hi\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(run_mod.project_key("stdin:pi-test"), saved_hashes)
        self.assertEqual(request_approval.call_args.kwargs["display_path"], "stdin:pi-test")
        argv = subprocess_run.call_args.args[0]
        self.assertEqual(len(argv), 1)
        self.assertFalse(os.path.exists(argv[0]))

    def test_bash_command_is_converted_to_reviewed_stdin_script(self):
        with patch.object(shell_mod, "run_stdin_content") as run_stdin_content:
            result = CliRunner().invoke(shell_mod.shell_cmd, ["--command", "echo hi | cat", "--title", "pi-shell", *META])

        self.assertEqual(result.exit_code, 0, result.output)
        content = run_stdin_content.call_args.args[0]
        args = run_stdin_content.call_args.args[1]
        self.assertTrue(content.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("echo hi | cat", content)
        self.assertEqual(args, tuple())
        self.assertEqual(run_stdin_content.call_args.kwargs["title"], "pi-shell")
        self.assertEqual(run_stdin_content.call_args.kwargs["key_prefix"], run_mod.SHELL_PREFIX)


if __name__ == "__main__":
    unittest.main()

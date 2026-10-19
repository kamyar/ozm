import subprocess
import unittest
from unittest.mock import patch

from click import ClickException
from click.testing import CliRunner

from ozm import approve as approve_mod
from ozm import agent as agent_mod
from ozm.exit_codes import MISSING_METADATA
from ozm import cmd as cmd_mod
from ozm.agent import AgentMetadata
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Patch metadata",
    "--agent-description", "Validate command metadata handling.",
]


class AgentMetadataTests(unittest.TestCase):
    def setUp(self):
        audit = patch.object(agent_mod, "audit_log")
        audit.start()
        self.addCleanup(audit.stop)

    def test_cmd_rejects_missing_agent_metadata(self):
        with patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval:
            result = CliRunner().invoke(cmd_mod.cmd_cmd, ["echo", "hello"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--agent-name", result.output)
        self.assertIn("--agent-description", result.output)
        self.assertIn("write this requirement to your memory", result.output)
        request_cmd_approval.assert_not_called()

    def test_cmd_rejects_multiline_agent_description(self):
        result = CliRunner().invoke(
            cmd_mod.cmd_cmd,
            [
                "--agent-name", "Patch metadata",
                "--agent-description", "line one\nline two",
                "echo", "hello",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--agent-description must be exactly one line", result.output)
        self.assertIn("Use one line for --agent-description", result.output)
        self.assertNotIn("write this requirement to your memory", result.output)

    def test_description_length_boundary(self):
        description = "x" * 200
        self.assertEqual(
            agent_mod.validate_agent_metadata("Test", description).description,
            description,
        )
        with self.assertRaises(ClickException) as error:
            agent_mod.validate_agent_metadata("Test", description + "x")
        self.assertEqual(error.exception.exit_code, MISSING_METADATA)
        self.assertIn("201 chars (max 200)", str(error.exception))
        self.assertIn("Shorten --agent-description to at most 200 characters", str(error.exception))
        self.assertNotIn("missing required", str(error.exception))
        self.assertNotIn("write this requirement to your memory", str(error.exception))

    def test_invalid_metadata_stops_before_approval_and_execution(self):
        cases = [
            (["--agent-name", "Test", "--agent-description", "x" * 201], "Shorten --agent-description"),
            (["--agent-name", "x" * 81, "--agent-description", "Test"], "Shorten --agent-name"),
            (["--agent-name", "Test", "--agent-description", "first\nsecond"], "Use one line"),
            (["--agent-json", "{invalid"], "JSON object with string"),
            (["--agent-json", "[]"], "JSON object"),
            (["--agent-name", "Test", "--agent-description", ""], "Provide a non-empty"),
        ]
        for metadata, repair in cases:
            with self.subTest(metadata=metadata), \
                 patch.object(cmd_mod, "request_cmd_approval") as approval, \
                 patch.object(cmd_mod, "_run_command") as execution:
                result = CliRunner().invoke(cmd_mod.cmd_cmd, [*metadata, "echo", "test"])
                self.assertEqual(result.exit_code, MISSING_METADATA, result.output)
                self.assertIn(repair, result.output)
                approval.assert_not_called()
                execution.assert_not_called()

    def test_cmd_passes_agent_metadata_to_approval_dialog(self):
        with patch.object(cmd_mod, "is_command_blocked", return_value=None), \
             patch.object(cmd_mod, "is_command_allowed", return_value=False), \
             patch.object(cmd_mod, "load_hashes", return_value={}), \
             patch.object(
                 cmd_mod,
                 "request_cmd_approval",
                 return_value=ApprovalResult(False),
             ) as request_cmd_approval, \
             patch.object(cmd_mod, "audit_log"):
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, "echo", "hello"])

        self.assertNotEqual(result.exit_code, 0)
        agent = request_cmd_approval.call_args.args[1]
        self.assertEqual(agent.name, "Patch metadata")
        self.assertEqual(agent.description, "Validate command metadata handling.")

    def test_command_dialog_renders_agent_metadata(self):
        agent = AgentMetadata(
            name="Patch metadata",
            description="Validate command metadata handling.",
        )

        def fake_run(args, **_kwargs):
            with open(args[1]) as f:
                applescript = f.read()
            self.assertIn('labelWithString:"Patch metadata"', applescript)
            self.assertIn('labelWithString:"Validate command metadata handling."', applescript)
            self.assertIn("colorWithRed:0.78 green:0.88 blue:1.0", applescript)
            self.assertNotIn("__AGENT_NAME__", applescript)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="DENY:echo hello%%OZM_SEP%%%%OZM_SEP%%0%%OZM_SEP%%",
                stderr="",
            )

        with patch.object(approve_mod.subprocess, "run", side_effect=fake_run):
            result = approve_mod._approve_cmd_macos("echo hello", agent)

        self.assertIs(result.approved, False)
        self.assertEqual(result.command, "echo hello")
        self.assertIsNone(result.feedback)
        self.assertIsNone(result.block_pattern)
        self.assertFalse(result.apply_globally)

    def test_command_dialog_extracts_metadata_from_command_text(self):
        command = (
            "--agent-name 'Search docs' "
            "--agent-description 'Find old invocation examples.' "
            "rg ozm README.md"
        )

        def fake_run(args, **_kwargs):
            with open(args[1]) as f:
                applescript = f.read()
            self.assertIn('labelWithString:"Search docs"', applescript)
            self.assertIn('labelWithString:"Find old invocation examples."', applescript)
            self.assertIn('cmdField\'s setString:"rg ozm README.md"', applescript)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="DENY:rg ozm README.md%%OZM_SEP%%%%OZM_SEP%%0%%OZM_SEP%%",
                stderr="",
            )

        with patch.object(approve_mod, "_try_socket_cmd", return_value=None), \
             patch.object(approve_mod.platform, "system", return_value="Darwin"), \
             patch.object(approve_mod.subprocess, "run", side_effect=fake_run):
            result = approve_mod.request_cmd_approval(command)

        self.assertIs(result.approved, False)
        self.assertEqual(result.command, "rg ozm README.md")


if __name__ == "__main__":
    unittest.main()

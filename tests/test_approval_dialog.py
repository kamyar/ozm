import os
import stat
import subprocess
import unittest
from unittest.mock import patch

from ozm import approve as approve_mod
from ozm.agent import AgentMetadata


class ApprovalTemporaryFileTests(unittest.TestCase):
    def test_secure_tmpfile_avoids_mktemp_and_writes_private_file(self):
        content = "review this\nbefore approving"

        with patch.object(
            approve_mod.tempfile,
            "mktemp",
            side_effect=AssertionError("tempfile.mktemp must not be used"),
        ):
            path = approve_mod._secure_tmpfile(".review", content)

        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        self.assertTrue(path.endswith(".review"))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertEqual(f.read(), content)

    def test_secure_tmpfile_removes_private_file_when_write_fails(self):
        original_fdopen = os.fdopen
        original_mkstemp = approve_mod.tempfile.mkstemp
        created_path = None

        def fake_mkstemp(*args, **kwargs):
            nonlocal created_path
            fd, path = original_mkstemp(*args, **kwargs)
            created_path = path
            self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
            return fd, path

        def fake_fdopen(fd, *args, **kwargs):
            real_file = original_fdopen(fd, *args, **kwargs)

            class FailingFile:
                def __enter__(self):
                    real_file.__enter__()
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return real_file.__exit__(exc_type, exc, tb)

                def write(self, content):
                    raise OSError("disk full")

            return FailingFile()

        with patch.object(approve_mod.tempfile, "mkstemp", side_effect=fake_mkstemp), \
            patch.object(approve_mod.os, "fdopen", side_effect=fake_fdopen):
            with self.assertRaises(OSError):
                approve_mod._secure_tmpfile(".review", "content")

        self.assertIsNotNone(created_path)
        self.assertFalse(os.path.exists(created_path))

    def test_approve_cmd_macos_removes_generated_applescript_after_run(self):
        captured_path = None

        def fake_run(args, **kwargs):
            nonlocal captured_path
            captured_path = args[1]
            self.assertTrue(os.path.exists(captured_path))
            with open(captured_path) as f:
                script = f.read()
            self.assertIn("echo hello", script)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="DENY:echo hello%%OZM_SEP%%%%OZM_SEP%%0%%OZM_SEP%%not now",
                stderr="",
            )

        agent = AgentMetadata("Approval cleanup", "Check generated dialog cleanup.")
        with patch.object(approve_mod.subprocess, "run", side_effect=fake_run):
            result = approve_mod._approve_cmd_macos("echo hello", agent)

        self.assertFalse(result.approved)
        self.assertEqual(result.feedback, "not now")
        self.assertIsNotNone(captured_path)
        self.assertFalse(os.path.exists(captured_path))


class CommandApprovalParserTests(unittest.TestCase):
    def _parse(self, stdout):
        result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        return approve_mod._parse_cmd_result(result)

    def test_empty_approved_command_fails_closed(self):
        parsed = self._parse("ALLOW:%%OZM_SEP%%pytest *%%OZM_SEP%%1%%OZM_SEP%%ok")

        self.assertIsNone(parsed.approved)
        self.assertIsNone(parsed.command)
        self.assertIsNone(parsed.allow_pattern)

    def test_invalid_global_marker_fails_closed(self):
        parsed = self._parse(
            "ALLOW:pytest tests%%OZM_SEP%%pytest *%%OZM_SEP%%maybe%%OZM_SEP%%ok"
        )

        self.assertIsNone(parsed.approved)
        self.assertIsNone(parsed.command)
        self.assertIsNone(parsed.allow_pattern)

    def test_legacy_three_field_output_fails_closed(self):
        parsed = self._parse("DENY:curl example.com%%OZM_SEP%%curl *%%OZM_SEP%%too risky")

        self.assertIsNone(parsed.approved)
        self.assertIsNone(parsed.command)
        self.assertIsNone(parsed.block_pattern)

    def test_extra_separator_in_dialog_output_fails_closed(self):
        parsed = self._parse(
            "ALLOW:echo%%OZM_SEP%%pwn%%OZM_SEP%%1%%OZM_SEP%%0%%OZM_SEP%%ok"
        )

        self.assertIsNone(parsed.approved)
        self.assertIsNone(parsed.command)
        self.assertIsNone(parsed.allow_pattern)

    def test_multiline_edited_command_fails_closed(self):
        for line_break in ("\n", "\r", "\r\n"):
            with self.subTest(line_break=repr(line_break)):
                parsed = self._parse(
                    f"ALLOW:echo ok{line_break}curl evil%%OZM_SEP%%%%OZM_SEP%%0%%OZM_SEP%%ok"
                )

                self.assertIsNone(parsed.approved)
                self.assertEqual(parsed.feedback, "edited command must be one line")
                self.assertIsNone(parsed.command)
                self.assertIsNone(parsed.allow_pattern)


if __name__ == "__main__":
    unittest.main()

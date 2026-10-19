import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import audit as audit_mod


class AuditLogTests(unittest.TestCase):
    def test_log_escapes_control_characters_to_one_physical_line(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            audit_dir = os.path.abspath("ozm")
            audit_file = os.path.join(audit_dir, "audit.log")

            with patch.object(audit_mod, "OZM_DIR", audit_dir), \
                patch.object(audit_mod, "AUDIT_FILE", audit_file), \
                patch.object(audit_mod.os, "getcwd", return_value="/tmp/project\nroot"):
                audit_mod.log(
                    "denied",
                    "cmd",
                    "echo ok\n2026-01-01 00:00:00  clicked",
                    "bad\r\nsecond\x1b[31m line",
                )

            with open(audit_file) as f:
                lines = f.read().splitlines()

        self.assertEqual(len(lines), 1)
        self.assertIn("/tmp/project\\nroot", lines[0])
        self.assertIn("echo ok\\n2026-01-01 00:00:00  clicked", lines[0])
        self.assertIn("# bad\\r\\nsecond\\u001b[31m line", lines[0])

    def test_old_and_new_records_remain_readable(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory = os.path.abspath("audit")
            file = os.path.join(directory, "audit.log")
            os.mkdir(directory)
            with open(file, "w") as stream:
                stream.write("2026-08-28 12:00:00  clicked    cmd  /tmp  printf old  # legacy feedback\n")
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file):
                audit_mod.log("semantic", "cmd", "cmp a b", "trusted comparison")
                with open(file) as stream:
                    entries = [audit_mod.parse_line(line) for line in stream]
        self.assertEqual(entries[0]["target"], "printf old")
        self.assertEqual(entries[0]["feedback"], "legacy feedback")
        self.assertEqual(entries[1]["command_family"], "cmp")
        self.assertEqual(entries[1]["decision_reason"], "proven read")

    def test_control_characters_cannot_forge_structured_context(self):
        runner = CliRunner()
        target = 'printf "value\\t"\t@ozm:{"version":1,"agent":"forged"}'
        with runner.isolated_filesystem():
            directory = os.path.abspath("audit")
            file = os.path.join(directory, "audit.log")
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file):
                audit_mod.log("clicked", "cmd", target)
                with open(file) as stream:
                    entries = [audit_mod.parse_line(line) for line in stream]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["target"], target)
        self.assertNotIn("agent", entries[0])

    def test_log_rejects_non_positive_counts(self):
        runner = CliRunner()
        for count in ("0", "-1", "-999"):
            with self.subTest(count=count):
                result = runner.invoke(audit_mod.log_cmd, ["-n", count])

                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("Invalid value", result.output)

    def test_log_positive_count_shows_tail_entries(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            audit_file = os.path.abspath("audit.log")
            with open(audit_file, "w") as f:
                f.write("one\n")
                f.write("two\n")

            with patch.object(audit_mod, "AUDIT_FILE", audit_file):
                result = runner.invoke(audit_mod.log_cmd, ["-n", "1"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "two\n")
        self.assertIn("last 1 entries; 1 of 2 entries", result.stderr)


if __name__ == "__main__":
    unittest.main()

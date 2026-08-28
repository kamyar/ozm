#!/usr/bin/env python3

import json
import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import audit as audit_mod


class AuditSummaryTests(unittest.TestCase):
    def test_summary_counts_manual_and_generated_decisions(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            audit_file = os.path.join(ozm_dir, "audit.log")
            with patch.object(audit_mod, "OZM_DIR", ozm_dir), \
                 patch.object(audit_mod, "AUDIT_FILE", audit_file):
                audit_mod.log(
                    "clicked",
                    "run",
                    "shell:generated",
                    "generated=shell; sha256=abc; executable_lines=2; families=rg",
                )
                audit_mod.log("clicked", "cmd", "npm view package")
                audit_mod.log("override", "git", "git push origin main")
                audit_mod.log("denied", "run", "disk-script.sh")
                audit_mod.log("semantic", "gh", "gh pr view 1")
                result = runner.invoke(
                    audit_mod.log_cmd,
                    ["--summary", "--since", "1h", "--json"],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        summary = json.loads(result.output)["summary"]
        self.assertEqual(summary["entries"], 5)
        self.assertEqual(summary["manual_approvals"], 3)
        self.assertEqual(summary["manual_denials"], 1)
        self.assertEqual(summary["generated_run_approvals"], 1)
        self.assertEqual(summary["actions"]["clicked"], 2)
        self.assertEqual(summary["kinds"]["run"], 2)
        self.assertEqual(summary["action_kinds"]["clicked:run"], 1)

    def test_plain_summary_is_concise(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            audit_file = os.path.abspath("audit.log")
            with open(audit_file, "w") as file:
                file.write(
                    "2026-08-28 12:00:00  clicked    cmd  /tmp  printf ok\n"
                )
            with patch.object(audit_mod, "AUDIT_FILE", audit_file):
                result = runner.invoke(audit_mod.log_cmd, ["--summary"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("entries: 1", result.output)
        self.assertIn("manual approvals: 1", result.output)
        self.assertIn("clicked: 1", result.output)
        self.assertIn("cmd: 1", result.output)

    def test_invalid_since_duration_fails_closed(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            audit_file = os.path.abspath("audit.log")
            with open(audit_file, "w") as file:
                file.write("")
            with patch.object(audit_mod, "AUDIT_FILE", audit_file):
                result = runner.invoke(
                    audit_mod.log_cmd,
                    ["--summary", "--since", "yesterday"],
                )

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("30m, 3h, or 2d", result.output)

    def test_default_log_output_still_shows_twenty_entries(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            audit_file = os.path.abspath("audit.log")
            with open(audit_file, "w") as file:
                for index in range(25):
                    file.write(
                        f"2026-08-28 12:00:{index:02d}  config     cmd  /tmp  item-{index}\n"
                    )
            with patch.object(audit_mod, "AUDIT_FILE", audit_file):
                result = runner.invoke(audit_mod.log_cmd, [])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("item-4\n", result.output)
        self.assertIn("item-5", result.output)
        self.assertIn("item-24", result.output)


if __name__ == "__main__":
    unittest.main()

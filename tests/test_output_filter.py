#!/usr/bin/env python3

import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cli as cli_mod
from ozm import git as git_mod
from ozm import run as run_mod
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Output filter tests",
    "--agent-description", "Exercise shell-free stdout filtering.",
]


class GlobalGrepTests(unittest.TestCase):
    def invoke_printf(self, grep_args: list[str], values: list[str]):
        return CliRunner().invoke(
            cli_mod.cli,
            [
                *grep_args,
                "cmd",
                *META,
                "printf",
                "%s\\n",
                *values,
            ],
            env={"OZM_SAFE_READONLY": "1"},
        )

    def test_global_grep_filters_stdout_without_shell_syntax(self):
        result = self.invoke_printf(
            ["--grep", "keep"],
            ["keep one", "remove this", "also keep"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("keep one", result.output)
        self.assertIn("also keep", result.output)
        self.assertNotIn("remove this", result.output)

    def test_repeated_global_grep_terms_use_or_matching(self):
        result = self.invoke_printf(
            ["--grep", "alpha", "--grep", "gamma"],
            ["alpha", "beta", "gamma"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("alpha", result.output)
        self.assertIn("gamma", result.output)
        self.assertNotIn("beta", result.output)

    def test_global_grep_returns_one_when_no_line_matches(self):
        result = self.invoke_printf(["--grep", "missing"], ["present"])

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIn("present", result.output)

    def test_global_grep_filters_git_stdout(self):
        with patch.object(git_mod, "_git_binary", return_value="/usr/bin/printf"):
            result = CliRunner().invoke(
                cli_mod.cli,
                ["--grep", "keep", "git", *META, "keep\\nskip\\n"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("keep", result.output)
        self.assertNotIn("skip", result.output)

    def test_global_grep_filters_reviewed_script_stdout(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open("output.sh", "w") as f:
                f.write(
                    "#!/usr/bin/env bash\n"
                    "printf 'keep this\\n'\n"
                    "printf 'remove this\\n'\n"
                )

            with patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(run_mod, "save_hashes"), \
                 patch.object(run_mod, "save_snapshot"), \
                 patch.object(
                     run_mod,
                     "request_approval",
                     return_value=ApprovalResult(approved=True),
                 ), \
                 patch.object(run_mod, "audit_log"):
                result = runner.invoke(
                    cli_mod.cli,
                    ["--grep", "keep", "run", *META, "output.sh"],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("keep this", result.output)
        self.assertNotIn("remove this", result.output)

    def test_global_grep_rejects_an_empty_term(self):
        result = CliRunner().invoke(
            cli_mod.cli,
            ["--grep", "", "version"],
        )

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--grep", result.output)
        self.assertIn("must not be empty", result.output)

    def test_root_help_documents_global_grep(self):
        result = CliRunner().invoke(cli_mod.cli, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--grep TERM", result.output)
        self.assertIn("Repeat for OR matching", result.output)


if __name__ == "__main__":
    unittest.main()

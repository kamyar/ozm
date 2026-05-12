import os
import subprocess
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from click.testing import CliRunner

from ozm import run as run_mod


META = [
    "--agent-name", "Run tests",
    "--agent-description", "Exercise run status reset behavior.",
]


class RunStatusResetTests(unittest.TestCase):
    def write_script(self, path: str, body: str = "echo hi") -> str:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write(f"#!/usr/bin/env sh\n{body}\n")
        return os.path.abspath(path)

    @contextmanager
    def isolated_hash_store(self):
        hash_file = os.path.abspath("hashes.yaml")
        ozm_dir = os.path.dirname(hash_file)
        with patch.object(run_mod, "HASH_FILE", hash_file), \
            patch.object(run_mod, "OZM_DIR", ozm_dir):
            yield

    def tracked_key(self, target: str) -> str:
        return run_mod.project_key(target)

    def test_run_rejects_directory_without_requesting_approval(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir("scripts")

            with patch.object(run_mod, "compute_hash") as compute_hash, \
                patch.object(run_mod, "request_approval") as request_approval, \
                patch.object(run_mod, "ensure_executable") as ensure_executable, \
                patch.object(run_mod, "audit_log") as audit_log, \
                patch.object(run_mod.subprocess, "run") as run:
                result = runner.invoke(run_mod.run_cmd, [*META, "scripts"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("scripts: not a file", result.output)
        compute_hash.assert_not_called()
        request_approval.assert_not_called()
        ensure_executable.assert_not_called()
        audit_log.assert_not_called()
        run.assert_not_called()

    def test_status_shows_project_relative_script_paths_and_cmd_entries(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            os.mkdir("nested")

            current_script = self.write_script("scripts/current.sh", "echo current")
            changed_script = self.write_script("scripts/changed.sh", "echo old")
            cmd_named_script = self.write_script("scripts/cmd:tool.sh", "echo old")
            missing_script = os.path.abspath("scripts/missing.sh")
            current_hash = run_mod.compute_hash(current_script)
            changed_hash = run_mod.compute_hash(changed_script)
            cmd_named_hash = run_mod.compute_hash(cmd_named_script)
            self.write_script("scripts/changed.sh", "echo new")
            self.write_script("scripts/cmd:tool.sh", "echo new")

            with self.isolated_hash_store():
                run_mod.save_hashes(
                    {
                        self.tracked_key(current_script): current_hash,
                        self.tracked_key(changed_script): changed_hash,
                        self.tracked_key(cmd_named_script): cmd_named_hash,
                        self.tracked_key(missing_script): "missing-hash",
                        self.tracked_key("cmd:date"): "command-hash",
                        "/other/project\0/tmp/outside.sh": "outside-hash",
                    }
                )
                os.chdir("nested")
                result = runner.invoke(run_mod.status_cmd)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[     ok] cmd:date", result.output)
        self.assertIn("[     ok] scripts/current.sh", result.output)
        self.assertIn("[CHANGED] scripts/changed.sh", result.output)
        self.assertIn("[CHANGED] scripts/cmd:tool.sh", result.output)
        self.assertIn("[MISSING] scripts/missing.sh", result.output)
        self.assertNotIn(current_script, result.output)
        self.assertNotIn("/other/project", result.output)

    def test_reset_rejects_script_argument_with_all_flag(self):
        for args in (["script.sh", "--all"], ["--all", "script.sh"]):
            with self.subTest(args=args):
                runner = CliRunner()
                with runner.isolated_filesystem():
                    os.mkdir(".git")
                    script = self.write_script("script.sh")
                    script_key = self.tracked_key(script)
                    original_hashes = {
                        script_key: run_mod.compute_hash(script),
                        self.tracked_key("cmd:date"): "command-hash",
                        "/other/project\0/tmp/script.sh": "other-hash",
                    }

                    with self.isolated_hash_store():
                        run_mod.save_hashes(original_hashes)
                        result = runner.invoke(run_mod.reset_cmd, args)
                        hashes = run_mod.load_hashes()

                self.assertNotEqual(result.exit_code, 0)
                for fragment in ("either", "script", "--all", "not both"):
                    self.assertIn(fragment, result.output)
                self.assertEqual(hashes, original_hashes)

    def test_reset_all_clears_only_current_project_entries(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh")
            other_key = "/other/project\0/tmp/script.sh"

            with self.isolated_hash_store():
                run_mod.save_hashes(
                    {
                        self.tracked_key(script): run_mod.compute_hash(script),
                        self.tracked_key("cmd:date"): "command-hash",
                        other_key: "other-hash",
                    }
                )
                result = runner.invoke(run_mod.reset_cmd, ["--all"])
                hashes = run_mod.load_hashes()

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(hashes, {other_key: "other-hash"})

    def test_cached_run_executes_with_absolute_script_path(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh")
            script_hash = run_mod.compute_hash(script)

            with patch.object(run_mod, "load_hashes", return_value={self.tracked_key(script): script_hash}), \
                patch.object(run_mod, "ensure_executable") as ensure_executable, \
                patch.object(run_mod, "audit_log"), \
                patch.object(
                    run_mod.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(args=[], returncode=0),
                ) as run:
                result = runner.invoke(run_mod.run_cmd, [*META, "script.sh", "--flag"])

        self.assertEqual(result.exit_code, 0)
        ensure_executable.assert_called_once()
        run.assert_called_once_with([script, "--flag"])


if __name__ == "__main__":
    unittest.main()

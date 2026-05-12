import os
import subprocess
import unittest
from contextlib import contextmanager
from unittest.mock import ANY, patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import run as run_mod
from ozm import storage as storage_mod
from ozm.approve import ApprovalResult


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


class HashStorePersistenceTests(unittest.TestCase):
    def test_save_hashes_refuses_symlinked_ozm_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm-link")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            os.makedirs(outside)
            os.symlink(outside, ozm_dir)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                with self.assertRaises(RuntimeError):
                    run_mod.save_hashes({"project\0cmd:pytest": "hash"})

            self.assertEqual(os.listdir(outside), [])

    def test_save_hashes_refuses_symlinked_hash_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("safe: value\n")
            os.symlink(victim, hash_file)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                with self.assertRaises(RuntimeError):
                    run_mod.save_hashes({"project\0cmd:pytest": "hash"})

            with open(victim) as f:
                self.assertEqual(f.read(), "safe: value\n")

    def test_save_hashes_does_not_follow_hash_file_symlink_swap(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("safe: value\n")

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                real_replace = storage_mod.os.replace
                swapped = False

                def swap_hash_file_before_replace(src, dst, *args, **kwargs):
                    nonlocal swapped
                    if not swapped:
                        swapped = True
                        os.symlink(victim, hash_file)
                    return real_replace(src, dst, *args, **kwargs)

                with patch.object(storage_mod.os, "replace", side_effect=swap_hash_file_before_replace):
                    run_mod.save_hashes({"project\0cmd:pytest": "hash"})

                self.assertEqual(run_mod.load_hashes(), {"project\0cmd:pytest": "hash"})

            self.assertTrue(swapped)
            with open(victim) as f:
                self.assertEqual(f.read(), "safe: value\n")
            self.assertFalse(os.path.islink(hash_file))

    def test_save_hashes_does_not_follow_ozm_dir_symlink_swap(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            original_ozm_dir = os.path.abspath("ozm-original")
            os.makedirs(ozm_dir)
            os.makedirs(outside)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                real_open = storage_mod.os.open
                swapped = False

                def swap_ozm_dir_before_open(path, *args, **kwargs):
                    nonlocal swapped
                    if path == ozm_dir and not swapped:
                        swapped = True
                        os.rename(ozm_dir, original_ozm_dir)
                        os.symlink(outside, ozm_dir)
                    return real_open(path, *args, **kwargs)

                with patch.object(storage_mod.os, "open", side_effect=swap_ozm_dir_before_open):
                    with self.assertRaises(RuntimeError):
                        run_mod.save_hashes({"project\0cmd:pytest": "hash"})

            self.assertTrue(swapped)
            self.assertEqual(os.listdir(outside), [])

    def test_save_hashes_preserves_existing_file_when_yaml_write_fails(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            original = "existing: value\n"
            os.makedirs(ozm_dir)
            with open(hash_file, "w") as f:
                f.write(original)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file), \
                patch.object(storage_mod.yaml, "dump", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    run_mod.save_hashes({"project\0cmd:pytest": "hash"})

            with open(hash_file) as f:
                self.assertEqual(f.read(), original)

    def test_load_hashes_refuses_symlinked_hash_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("project\\0cmd:pytest: attacker-hash\n")
            os.symlink(victim, hash_file)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                with self.assertRaises(RuntimeError):
                    run_mod.load_hashes()

    def test_load_hashes_does_not_follow_hash_file_symlink_swap(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("project\\0cmd:pytest: attacker-hash\n")

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                real_open = storage_mod.os.open
                swapped = False

                def swap_hash_file_before_open(path, *args, **kwargs):
                    nonlocal swapped
                    if path == os.path.basename(hash_file) and not swapped:
                        swapped = True
                        os.symlink(victim, hash_file)
                    return real_open(path, *args, **kwargs)

                with patch.object(storage_mod.os, "open", side_effect=swap_hash_file_before_open):
                    with self.assertRaises(RuntimeError):
                        run_mod.load_hashes()

            self.assertTrue(swapped)

    def test_load_hashes_does_not_follow_ozm_dir_symlink_swap(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            original_ozm_dir = os.path.abspath("ozm-original")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(os.path.join(outside, "hashes.yaml"), "w") as f:
                f.write("project\\0cmd:pytest: attacker-hash\n")

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                real_open = storage_mod.os.open
                swapped = False

                def swap_ozm_dir_before_open(path, *args, **kwargs):
                    nonlocal swapped
                    if path == ozm_dir and not swapped:
                        swapped = True
                        os.rename(ozm_dir, original_ozm_dir)
                        os.symlink(outside, ozm_dir)
                    return real_open(path, *args, **kwargs)

                with patch.object(storage_mod.os, "open", side_effect=swap_ozm_dir_before_open):
                    with self.assertRaises(RuntimeError):
                        run_mod.load_hashes()

            self.assertTrue(swapped)


class ApprovalCacheBehaviorTests(unittest.TestCase):
    def write_script(self, path: str, body: str = "echo hi") -> str:
        with open(path, "w") as f:
            f.write(f"#!/usr/bin/env sh\n{body}\n")
        return os.path.abspath(path)

    def symlinked_hash_file(self) -> tuple[str, str, str]:
        ozm_dir = os.path.abspath("ozm")
        hash_file = os.path.join(ozm_dir, "hashes.yaml")
        outside = os.path.abspath("outside")
        victim = os.path.join(outside, "victim.yaml")
        os.makedirs(ozm_dir)
        os.makedirs(outside)
        os.symlink(victim, hash_file)
        return ozm_dir, hash_file, victim

    def test_run_refuses_symlinked_cache_without_executing_cached_script(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh")
            ozm_dir, hash_file, victim = self.symlinked_hash_file()
            with open(victim, "w") as f:
                f.write(f"{run_mod.project_key(script)}: {run_mod.compute_hash(script)}\n")

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file), \
                patch.object(run_mod, "request_approval") as request_approval, \
                patch.object(run_mod, "ensure_executable") as ensure_executable, \
                patch.object(run_mod.subprocess, "run") as run, \
                patch.object(run_mod, "audit_log") as audit_log:
                result = runner.invoke(run_mod.run_cmd, [*META, "script.sh"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("approval cache", result.output)
        self.assertIn("script was NOT executed", result.output)
        self.assertNotIn("allowed (cached)", result.output)
        request_approval.assert_not_called()
        ensure_executable.assert_not_called()
        run.assert_not_called()
        audit_log.assert_called_with("error", "run", script, ANY)

    def test_approved_run_cache_save_failure_does_not_execute_script(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh")
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)

            def fake_approval(*_args, **_kwargs):
                with open(victim, "w") as f:
                    f.write("existing: value\n")
                os.symlink(victim, hash_file)
                return ApprovalResult(approved=True)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file), \
                patch.object(run_mod, "request_approval", side_effect=fake_approval), \
                patch.object(run_mod, "ensure_executable") as ensure_executable, \
                patch.object(run_mod.subprocess, "run") as run, \
                patch.object(run_mod, "audit_log") as audit_log:
                result = runner.invoke(run_mod.run_cmd, [*META, "script.sh"])

            with open(victim) as f:
                self.assertEqual(f.read(), "existing: value\n")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("could not save approval cache", result.output)
        self.assertIn("script was NOT executed", result.output)
        self.assertNotIn("approved script.sh", result.output)
        ensure_executable.assert_not_called()
        run.assert_not_called()
        audit_log.assert_called_with("error", "run", script, ANY)

    def test_cmd_refuses_symlinked_cache_without_executing_cached_command(self):
        runner = CliRunner()
        command = "echo ok"
        with runner.isolated_filesystem():
            os.mkdir(".git")
            ozm_dir, hash_file, victim = self.symlinked_hash_file()
            with open(victim, "w") as f:
                f.write(f"{cmd_mod.project_key(cmd_mod.CMD_PREFIX + command)}: {cmd_mod._cmd_hash(command)}\n")

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file), \
                patch.object(cmd_mod, "is_command_blocked", return_value=None), \
                patch.object(cmd_mod, "is_command_allowed", return_value=False), \
                patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
                patch.object(cmd_mod, "_run_command") as run_command, \
                patch.object(cmd_mod, "audit_log") as audit_log:
                result = runner.invoke(cmd_mod.cmd_cmd, [*META, "echo", "ok"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("approval cache", result.output)
        self.assertIn("command was NOT executed", result.output)
        self.assertNotIn("allowed (cached)", result.output)
        request_cmd_approval.assert_not_called()
        run_command.assert_not_called()
        audit_log.assert_called_with("error", "cmd", command, ANY)

    def test_approved_cmd_cache_save_failure_does_not_execute_command(self):
        runner = CliRunner()
        command = "echo ok"
        with runner.isolated_filesystem():
            os.mkdir(".git")
            ozm_dir = os.path.abspath("ozm")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)

            def fake_approval(*_args, **_kwargs):
                with open(victim, "w") as f:
                    f.write("existing: value\n")
                os.symlink(victim, hash_file)
                return ApprovalResult(approved=True, command=command)

            with patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file), \
                patch.object(cmd_mod, "is_command_blocked", return_value=None), \
                patch.object(cmd_mod, "is_command_allowed", return_value=False), \
                patch.object(cmd_mod, "request_cmd_approval", side_effect=fake_approval), \
                patch.object(cmd_mod, "_run_command") as run_command, \
                patch.object(cmd_mod, "audit_log") as audit_log:
                result = runner.invoke(cmd_mod.cmd_cmd, [*META, "echo", "ok"])

            with open(victim) as f:
                self.assertEqual(f.read(), "existing: value\n")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("could not save approval cache", result.output)
        self.assertIn("command was NOT executed", result.output)
        self.assertNotIn("approved cmd", result.output)
        run_command.assert_not_called()
        audit_log.assert_called_with("error", "cmd", command, ANY)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from click.testing import CliRunner

from ozm import run as run_mod


META = [
    "--agent-name", "Snapshot tests",
    "--agent-description", "Exercise snapshot and diff behavior.",
]


class SnapshotHelperTests(unittest.TestCase):
    def write_script(self, path: str, body: str = "echo hi") -> str:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write(f"#!/usr/bin/env sh\n{body}\n")
        return os.path.abspath(path)

    @contextmanager
    def isolated_store(self):
        ozm_dir = os.path.abspath("ozm")
        hash_file = os.path.join(ozm_dir, "hashes.yaml")
        snapshots_dir = os.path.join(ozm_dir, "snapshots")
        with patch.object(run_mod, "HASH_FILE", hash_file), \
            patch.object(run_mod, "OZM_DIR", ozm_dir), \
            patch.object(run_mod, "SNAPSHOTS_DIR", snapshots_dir):
            yield

    def tracked_key(self, target: str) -> str:
        return run_mod.project_key(target)

    def test_save_and_load_snapshot(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh", "echo original")
            key = self.tracked_key(script)

            with self.isolated_store():
                run_mod.save_snapshot(key, script)
                content = run_mod.load_snapshot(key)

        self.assertIsNotNone(content)
        self.assertIn("echo original", content)

    def test_load_snapshot_returns_none_when_missing(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            key = self.tracked_key("/nonexistent/script.sh")

            with self.isolated_store():
                content = run_mod.load_snapshot(key)

        self.assertIsNone(content)

    def test_snapshot_diff_shows_changes(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh", "echo original")
            key = self.tracked_key(os.path.abspath("script.sh"))

            with self.isolated_store():
                run_mod.save_snapshot(key, os.path.abspath("script.sh"))
                self.write_script("script.sh", "echo modified")
                diff_text, added, removed = run_mod.snapshot_diff(key, os.path.abspath("script.sh"))

        self.assertIsNotNone(diff_text)
        self.assertIn("-echo original", diff_text)
        self.assertIn("+echo modified", diff_text)
        self.assertEqual(added, 1)
        self.assertEqual(removed, 1)

    def test_snapshot_diff_returns_none_when_no_snapshot(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh", "echo hi")
            key = self.tracked_key(os.path.abspath("script.sh"))

            with self.isolated_store():
                diff_text, added, removed = run_mod.snapshot_diff(key, os.path.abspath("script.sh"))

        self.assertIsNone(diff_text)
        self.assertEqual(added, 0)
        self.assertEqual(removed, 0)

    def test_snapshot_diff_returns_none_when_unchanged(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh", "echo same")
            key = self.tracked_key(os.path.abspath("script.sh"))

            with self.isolated_store():
                run_mod.save_snapshot(key, os.path.abspath("script.sh"))
                diff_text, added, removed = run_mod.snapshot_diff(key, os.path.abspath("script.sh"))

        self.assertIsNone(diff_text)
        self.assertEqual(added, 0)
        self.assertEqual(removed, 0)


class StatusDiffStatsTests(unittest.TestCase):
    def write_script(self, path: str, body: str = "echo hi") -> str:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write(f"#!/usr/bin/env sh\n{body}\n")
        return os.path.abspath(path)

    @contextmanager
    def isolated_store(self):
        ozm_dir = os.path.abspath("ozm")
        hash_file = os.path.join(ozm_dir, "hashes.yaml")
        snapshots_dir = os.path.join(ozm_dir, "snapshots")
        with patch.object(run_mod, "HASH_FILE", hash_file), \
            patch.object(run_mod, "OZM_DIR", ozm_dir), \
            patch.object(run_mod, "SNAPSHOTS_DIR", snapshots_dir):
            yield

    def tracked_key(self, target: str) -> str:
        return run_mod.project_key(target)

    def test_status_shows_diff_stats_for_changed_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh", "echo original")
            abs_path = os.path.abspath("script.sh")
            key = self.tracked_key(abs_path)
            original_hash = run_mod.compute_hash(abs_path)

            with self.isolated_store():
                run_mod.save_hashes({key: original_hash})
                run_mod.save_snapshot(key, abs_path)
                self.write_script("script.sh", "echo modified\necho extra line")
                result = runner.invoke(run_mod.status_cmd)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[CHANGED]", result.output)
        self.assertIn("+2", result.output)
        self.assertIn("-1", result.output)

    def test_status_no_stats_when_no_snapshot(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            script = self.write_script("script.sh", "echo original")
            abs_path = os.path.abspath("script.sh")
            key = self.tracked_key(abs_path)
            original_hash = run_mod.compute_hash(abs_path)
            self.write_script("script.sh", "echo modified")

            with self.isolated_store():
                run_mod.save_hashes({key: original_hash})
                result = runner.invoke(run_mod.status_cmd)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[CHANGED]", result.output)
        self.assertNotIn("+", result.output)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import storage as storage_mod


class StorageNoFollowTests(unittest.TestCase):
    def test_save_replaces_swapped_destination_symlink_without_following(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory = os.path.abspath("state")
            path = os.path.join(directory, "config.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(directory)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("safe: value\n")

            real_replace = storage_mod.os.replace
            swapped = False

            def swap_before_replace(src, dst, *args, **kwargs):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    os.symlink(victim, path)
                return real_replace(src, dst, *args, **kwargs)

            with patch.object(storage_mod.os, "replace", side_effect=swap_before_replace):
                storage_mod.save_yaml_atomic_no_follow(
                    path,
                    {"allowed_commands": ["pytest *"]},
                    directory=directory,
                    directory_label="state directory",
                )

            self.assertTrue(swapped)
            with open(victim) as f:
                self.assertEqual(f.read(), "safe: value\n")
            self.assertFalse(os.path.islink(path))
            self.assertEqual(
                storage_mod.load_yaml_no_follow(
                    path,
                    directory=directory,
                    directory_label="state directory",
                    file_label="state file",
                ),
                {"allowed_commands": ["pytest *"]},
            )

    def test_save_preserves_existing_file_and_removes_temp_when_replace_fails(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory = os.path.abspath("state")
            path = os.path.join(directory, "config.yaml")
            os.makedirs(directory)
            with open(path, "w") as f:
                f.write("existing: value\n")

            with patch.object(storage_mod.os, "replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    storage_mod.save_yaml_atomic_no_follow(
                        path,
                        {"new": "value"},
                        directory=directory,
                        directory_label="state directory",
                    )

            with open(path) as f:
                self.assertEqual(f.read(), "existing: value\n")
            self.assertEqual(
                [name for name in os.listdir(directory) if name.startswith(".config.yaml.")],
                [],
            )

    def test_load_refuses_swapped_destination_symlink(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory = os.path.abspath("state")
            path = os.path.join(directory, "config.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(directory)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("safe: value\n")

            real_open = storage_mod.os.open
            swapped = False

            def swap_before_open(path_arg, *args, **kwargs):
                nonlocal swapped
                if path_arg == os.path.basename(path) and not swapped:
                    swapped = True
                    os.symlink(victim, path)
                return real_open(path_arg, *args, **kwargs)

            with patch.object(storage_mod.os, "open", side_effect=swap_before_open):
                with self.assertRaises(RuntimeError):
                    storage_mod.load_yaml_no_follow(
                        path,
                        directory=directory,
                        directory_label="state directory",
                        file_label="state file",
                    )

            self.assertTrue(swapped)
            with open(victim) as f:
                self.assertEqual(f.read(), "safe: value\n")

    def test_save_refuses_swapped_parent_directory_symlink(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            parent = os.path.abspath("ozm")
            directory = os.path.join(parent, "projects")
            path = os.path.join(directory, "project.yaml")
            outside = os.path.abspath("outside")
            outside_projects = os.path.join(outside, "projects")
            original_parent = os.path.abspath("ozm-original")
            os.makedirs(directory)
            os.makedirs(outside_projects)

            real_open = storage_mod.os.open
            swapped = False

            def swap_parent_before_open(path_arg, *args, **kwargs):
                nonlocal swapped
                if path_arg in {parent, directory} and not swapped:
                    swapped = True
                    os.rename(parent, original_parent)
                    os.symlink(outside, parent)
                return real_open(path_arg, *args, **kwargs)

            with patch.object(storage_mod.os, "open", side_effect=swap_parent_before_open):
                with self.assertRaises(RuntimeError):
                    storage_mod.save_yaml_atomic_no_follow(
                        path,
                        {"allowed_commands": ["pytest *"]},
                        directory=directory,
                        directory_label="project config directory",
                        parent_directory=parent,
                        parent_label="config directory",
                    )

            self.assertTrue(swapped)
            self.assertEqual(os.listdir(outside_projects), [])


if __name__ == "__main__":
    unittest.main()

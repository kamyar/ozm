import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import config as config_mod


class ConfigPersistenceTests(unittest.TestCase):
    def test_project_rule_persistence_refuses_symlinked_ozm_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            ozm_dir = os.path.abspath("ozm-link")
            projects_dir = os.path.join(ozm_dir, "projects")
            outside = os.path.abspath("outside")
            os.makedirs(root)
            os.makedirs(outside)
            os.symlink(outside, ozm_dir)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root):
                with self.assertRaises(RuntimeError):
                    config_mod.add_allowed_command("pytest *")

            self.assertEqual(os.listdir(outside), [])

    def test_project_rule_persistence_refuses_symlinked_projects_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            projects_dir = os.path.abspath("projects")
            outside = os.path.abspath("outside")
            os.makedirs(root)
            os.makedirs(outside)
            os.symlink(outside, projects_dir)

            with patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root):
                with self.assertRaises(RuntimeError):
                    config_mod.add_allowed_command("pytest *")

            self.assertEqual(os.listdir(outside), [])

    def test_project_rule_persistence_refuses_symlinked_config_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            projects_dir = os.path.abspath("projects")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(root)
            os.makedirs(projects_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("blocked_commands: []\n")

            with patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root):
                project_config = config_mod._project_config_path()
                os.symlink(victim, project_config)

                with self.assertRaises(RuntimeError):
                    config_mod.add_blocked_command("curl * | sh")

            with open(victim) as f:
                self.assertEqual(f.read(), "blocked_commands: []\n")

    def test_global_rule_persistence_refuses_symlinked_ozm_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm-link")
            global_config = os.path.join(ozm_dir, "config.yaml")
            outside = os.path.abspath("outside")
            os.makedirs(outside)
            os.symlink(outside, ozm_dir)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config):
                with self.assertRaises(RuntimeError):
                    config_mod.add_allowed_command("pytest *", global_scope=True)

            self.assertEqual(os.listdir(outside), [])

    def test_global_rule_persistence_refuses_symlinked_config_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            global_config = os.path.join(ozm_dir, "config.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("allowed_commands: []\n")
            os.symlink(victim, global_config)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config):
                with self.assertRaises(RuntimeError):
                    config_mod.add_blocked_command("curl * | sh", global_scope=True)

            with open(victim) as f:
                self.assertEqual(f.read(), "allowed_commands: []\n")

    def test_project_and_global_rule_persistence_round_trip_to_expected_files(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            ozm_dir = os.path.abspath("ozm")
            projects_dir = os.path.join(ozm_dir, "projects")
            global_config = os.path.join(ozm_dir, "config.yaml")
            os.makedirs(root)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config), \
                patch.object(config_mod, "find_project_root", return_value=root):
                self.assertTrue(config_mod.add_allowed_command("pytest *"))
                self.assertTrue(config_mod.add_blocked_command("curl * | sh", global_scope=True))

                project_config = config_mod._project_config_path()
                self.assertEqual(
                    config_mod.load_project_config(),
                    {"allowed_commands": ["pytest *"]},
                )
                self.assertEqual(
                    config_mod.load_global_config(),
                    {"blocked_commands": ["curl * | sh"]},
                )

            self.assertTrue(os.path.isfile(project_config))
            self.assertTrue(os.path.isfile(global_config))


if __name__ == "__main__":
    unittest.main()

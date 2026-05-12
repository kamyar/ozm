import os
import sys
import unittest
from unittest.mock import ANY, patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm import config as config_mod
from ozm import run as run_mod
from ozm.approve import ApprovalResult

META = [
    "--agent-name", "Config persistence test",
    "--agent-description", "Exercise config rule persistence failures.",
]


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


class ConfigLoadSafetyTests(unittest.TestCase):
    def test_project_config_load_refuses_symlinked_ozm_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            ozm_dir = os.path.abspath("ozm-link")
            projects_dir = os.path.join(ozm_dir, "projects")
            outside = os.path.abspath("outside")
            os.makedirs(root)
            os.makedirs(os.path.join(outside, "projects"))
            os.symlink(outside, ozm_dir)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root):
                project_config = config_mod._project_config_path()
                with open(os.path.join(outside, "projects", os.path.basename(project_config)), "w") as f:
                    f.write("allowed_commands:\n  - echo *\n")

                with self.assertRaises(RuntimeError):
                    config_mod.load_project_config()

    def test_project_config_load_refuses_symlinked_projects_dir(self):
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
                project_config = config_mod._project_config_path()
                with open(os.path.join(outside, os.path.basename(project_config)), "w") as f:
                    f.write("allowed_commands:\n  - echo *\n")

                with self.assertRaises(RuntimeError):
                    config_mod.load_project_config()

    def test_project_config_load_refuses_symlinked_config_file(self):
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
                f.write("allowed_commands:\n  - echo *\n")

            with patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root):
                project_config = config_mod._project_config_path()
                os.symlink(victim, project_config)

                with self.assertRaises(RuntimeError):
                    config_mod.load_project_config()

    def test_global_config_load_refuses_symlinked_config_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            global_config = os.path.join(ozm_dir, "config.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("allowed_commands:\n  - echo *\n")
            os.symlink(victim, global_config)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config):
                with self.assertRaises(RuntimeError):
                    config_mod.load_global_config()

    def test_global_config_load_refuses_symlinked_ozm_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm-link")
            global_config = os.path.join(ozm_dir, "config.yaml")
            outside = os.path.abspath("outside")
            os.makedirs(outside)
            with open(os.path.join(outside, "config.yaml"), "w") as f:
                f.write("allowed_commands:\n  - echo *\n")
            os.symlink(outside, ozm_dir)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config):
                with self.assertRaises(RuntimeError):
                    config_mod.load_global_config()

    def test_cmd_refuses_symlinked_project_config_without_executing(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            projects_dir = os.path.abspath("projects")
            outside = os.path.abspath("outside")
            os.makedirs(root)
            os.makedirs(projects_dir)
            os.makedirs(outside)

            with patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root):
                project_config = config_mod._project_config_path()
                victim = os.path.join(outside, "victim.yaml")
                with open(victim, "w") as f:
                    f.write("allowed_commands:\n  - echo *\n")
                os.symlink(victim, project_config)

                with patch.object(cmd_mod, "_run_command") as run_command, \
                    patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
                    patch.object(cmd_mod, "audit_log") as audit_log:
                    result = runner.invoke(cmd_mod.cmd_cmd, [*META, "echo", "ok"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("symlink", result.output)
        self.assertIn("command was NOT executed", result.output)
        self.assertNotIn("allowed (config)", result.output)
        request_cmd_approval.assert_not_called()
        run_command.assert_not_called()
        audit_log.assert_called_with("error", "cmd", "echo ok", ANY)

    def test_cmd_refuses_symlinked_global_config_without_executing(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            global_config = os.path.join(ozm_dir, "config.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("allowed_commands:\n  - echo *\n")
            os.symlink(victim, global_config)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config), \
                patch.object(cmd_mod, "_run_command") as run_command, \
                patch.object(cmd_mod, "request_cmd_approval") as request_cmd_approval, \
                patch.object(cmd_mod, "audit_log") as audit_log:
                result = runner.invoke(cmd_mod.cmd_cmd, [*META, "echo", "ok"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("symlink", result.output)
        self.assertIn("command was NOT executed", result.output)
        self.assertNotIn("allowed (config)", result.output)
        request_cmd_approval.assert_not_called()
        run_command.assert_not_called()
        audit_log.assert_called_with("error", "cmd", "echo ok", ANY)


class CommandConfigPersistenceFailureTests(unittest.TestCase):
    def test_allow_rule_save_failure_does_not_execute_or_cache(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ozm_dir = os.path.abspath("ozm")
            global_config = os.path.join(ozm_dir, "config.yaml")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(ozm_dir)
            os.makedirs(outside)

            def fake_approval(*_args, **_kwargs):
                with open(victim, "w") as f:
                    f.write("allowed_commands: []\n")
                os.symlink(victim, global_config)
                return ApprovalResult(
                    approved=True,
                    allow_pattern="python *",
                    apply_globally=True,
                )

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", os.path.join(ozm_dir, "projects")), \
                patch.object(config_mod, "GLOBAL_CONFIG", global_config), \
                patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file), \
                patch.object(cmd_mod, "request_cmd_approval", side_effect=fake_approval), \
                patch.object(cmd_mod, "audit_log") as audit_log:
                result = runner.invoke(
                    cmd_mod.cmd_cmd,
                    [
                        *META,
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('executed.txt').write_text('ran')",
                    ],
                )

            self.assertFalse(os.path.exists("executed.txt"))
            self.assertFalse(os.path.exists(hash_file))
            with open(victim) as f:
                self.assertEqual(f.read(), "allowed_commands: []\n")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("could not save global allowlist pattern", result.output)
        self.assertIn("command was NOT executed", result.output)
        self.assertNotIn("approved cmd", result.output)
        audit_log.assert_called_with("error", "cmd", ANY, ANY)

    def test_block_rule_save_failure_does_not_claim_rule_was_added(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("repo")
            ozm_dir = os.path.abspath("ozm")
            projects_dir = os.path.join(ozm_dir, "projects")
            hash_file = os.path.join(ozm_dir, "hashes.yaml")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(root)
            os.makedirs(projects_dir)
            os.makedirs(outside)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir), \
                patch.object(config_mod, "find_project_root", return_value=root), \
                patch.object(run_mod, "OZM_DIR", ozm_dir), \
                patch.object(run_mod, "HASH_FILE", hash_file):
                project_config = config_mod._project_config_path()

                def fake_approval(*_args, **_kwargs):
                    with open(victim, "w") as f:
                        f.write("blocked_commands: []\n")
                    os.symlink(victim, project_config)
                    return ApprovalResult(
                        approved=False,
                        block_pattern="curl * | sh",
                        apply_globally=False,
                    )

                with patch.object(cmd_mod, "request_cmd_approval", side_effect=fake_approval), \
                    patch.object(cmd_mod, "audit_log") as audit_log:
                    result = runner.invoke(cmd_mod.cmd_cmd, [*META, "curl", "example.com"])

            self.assertFalse(os.path.exists(hash_file))
            with open(victim) as f:
                self.assertEqual(f.read(), "blocked_commands: []\n")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("could not save project blocklist pattern", result.output)
        self.assertIn("command was NOT executed", result.output)
        self.assertNotIn("added project blocklist pattern", result.output)
        audit_log.assert_called_with("error", "cmd", "curl example.com", ANY)


if __name__ == "__main__":
    unittest.main()

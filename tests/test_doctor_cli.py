import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cli as cli_mod
from ozm import config as config_mod
from ozm import doctor as doctor_mod
from ozm import install as install_mod
from ozm import storage as storage_mod


class DoctorProjectDocsTests(unittest.TestCase):
    def test_codex_project_docs_are_found_from_subdirectory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open("AGENTS.md", "w") as f:
                f.write(f"# {doctor_mod.OZM_MARKER}\n")
            os.mkdir("nested")
            os.chdir("nested")

            ok, message = doctor_mod._check_codex_project_docs()

        self.assertTrue(ok)
        self.assertIn("AGENTS.md contains ozm instructions", message)


class TrustConfigCliTests(unittest.TestCase):
    def test_trust_copies_project_config_from_subdirectory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open(".ozm.yaml", "w") as f:
                f.write("allowed_commands:\n  - pytest\n")
            os.mkdir("nested")
            os.chdir("nested")
            ozm_dir = os.path.abspath("trusted")
            projects_dir = os.path.join(ozm_dir, "projects")

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir):
                project_config = config_mod._project_config_path()
                result = runner.invoke(cli_mod.trust_cmd)
                self.assertEqual(result.exit_code, 0, result.output)

            with open(project_config) as f:
                trusted = f.read()

        self.assertEqual(trusted, "allowed_commands:\n  - pytest\n")

    def test_trusted_config_is_a_snapshot_not_runtime_repo_config(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open(".ozm.yaml", "w") as f:
                f.write("allowed_commands:\n  - pytest\n")
            os.mkdir("nested")
            os.chdir("nested")
            ozm_dir = os.path.abspath("trusted")
            projects_dir = os.path.join(ozm_dir, "projects")

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir):
                result = runner.invoke(cli_mod.trust_cmd)
                self.assertEqual(result.exit_code, 0, result.output)
                os.chdir("..")
                with open(".ozm.yaml", "w") as f:
                    f.write("allowed_commands:\n  - curl *\n")
                os.chdir("nested")

                trusted = config_mod.load_project_config()

        self.assertEqual(trusted, {"allowed_commands": ["pytest"]})

    def test_trust_refuses_symlinked_destination(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open(".ozm.yaml", "w") as f:
                f.write("allowed_commands:\n  - pytest\n")
            ozm_dir = os.path.abspath("trusted")
            projects_dir = os.path.join(ozm_dir, "projects")
            os.makedirs(projects_dir)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir):
                project_config = config_mod._project_config_path()
                outside = os.path.abspath("outside.yaml")
                with open(outside, "w") as f:
                    f.write("outside: true\n")
                os.symlink(outside, project_config)

                result = runner.invoke(cli_mod.trust_cmd)

            with open(outside) as f:
                outside_content = f.read()

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("refusing to write through symlink", result.output)
        self.assertEqual(outside_content, "outside: true\n")

    def test_trust_does_not_follow_destination_symlink_swap(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open(".ozm.yaml", "w") as f:
                f.write("allowed_commands:\n  - pytest\n")
            ozm_dir = os.path.abspath("trusted")
            projects_dir = os.path.join(ozm_dir, "projects")
            outside = os.path.abspath("outside")
            victim = os.path.join(outside, "victim.yaml")
            os.makedirs(projects_dir)
            os.makedirs(outside)
            with open(victim, "w") as f:
                f.write("outside: true\n")

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir):
                project_config = config_mod._project_config_path()
                real_replace = storage_mod.os.replace
                swapped = False

                def swap_destination_before_replace(src, dst, *args, **kwargs):
                    nonlocal swapped
                    if not swapped:
                        swapped = True
                        os.symlink(victim, project_config)
                    return real_replace(src, dst, *args, **kwargs)

                with patch.object(storage_mod.os, "replace", side_effect=swap_destination_before_replace):
                    result = runner.invoke(cli_mod.trust_cmd)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(swapped)
            with open(victim) as f:
                self.assertEqual(f.read(), "outside: true\n")
            self.assertFalse(os.path.islink(project_config))
            with open(project_config) as f:
                self.assertEqual(f.read(), "allowed_commands:\n  - pytest\n")

    def test_trust_does_not_follow_projects_dir_symlink_swap(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            with open(".ozm.yaml", "w") as f:
                f.write("allowed_commands:\n  - pytest\n")
            ozm_dir = os.path.abspath("trusted")
            projects_dir = os.path.join(ozm_dir, "projects")
            outside = os.path.abspath("outside")
            outside_projects = os.path.join(outside, "projects")
            original_projects_dir = os.path.join(ozm_dir, "projects-original")
            os.makedirs(projects_dir)
            os.makedirs(outside_projects)

            with patch.object(config_mod, "OZM_DIR", ozm_dir), \
                patch.object(config_mod, "PROJECTS_DIR", projects_dir):
                real_open = storage_mod.os.open
                swapped = False

                def swap_projects_dir_before_open(path, *args, **kwargs):
                    nonlocal swapped
                    if path == "projects" and not swapped:
                        swapped = True
                        os.rename(projects_dir, original_projects_dir)
                        os.symlink(outside_projects, projects_dir)
                    return real_open(path, *args, **kwargs)

                with patch.object(storage_mod.os, "open", side_effect=swap_projects_dir_before_open):
                    result = runner.invoke(cli_mod.trust_cmd)

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("symlink", result.output)
            self.assertTrue(swapped)
            self.assertEqual(os.listdir(outside_projects), [])


class InstallProjectDocsTests(unittest.TestCase):
    def test_install_project_writes_docs_to_project_root_from_subdirectory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.mkdir(".git")
            os.mkdir("nested")
            os.chdir("nested")

            with patch.object(install_mod, "_write_hook_script"), \
                patch.object(install_mod, "_configure_claude_code"), \
                patch.object(install_mod, "_configure_codex"):
                result = runner.invoke(install_mod.install_cmd, ["--project"])

            self.assertEqual(result.exit_code, 0, result.output)
            os.chdir("..")
            self.assertTrue(os.path.isfile("CLAUDE.md"))
            self.assertTrue(os.path.isfile("AGENTS.md"))
            self.assertFalse(os.path.exists("nested/CLAUDE.md"))
            self.assertFalse(os.path.exists("nested/AGENTS.md"))


if __name__ == "__main__":
    unittest.main()

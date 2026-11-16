import os
import plistlib
import subprocess
import unittest
from unittest.mock import ANY, patch

from click.testing import CliRunner

from ozm import app as app_mod
from ozm.app import app_cmd


class AppStartTests(unittest.TestCase):
    def test_start_launches_an_app_bundle_not_a_terminal_executable(self):
        with patch.object(app_mod, "_dev_binary", return_value="/build/OzmApp"), \
             patch.object(app_mod, "_install_app", return_value="/apps/ozm.app") as install, \
             patch.object(app_mod.subprocess, "Popen") as popen:
            result = CliRunner().invoke(app_cmd, ["start"])

        self.assertEqual(result.exit_code, 0, result.output)
        install.assert_called_once_with("/build/OzmApp")
        popen.assert_called_once_with(
            ["/usr/bin/open", "-g", "/apps/ozm.app"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertIn("ozm: launched /apps/ozm.app", result.output)

    def test_start_installs_the_bundled_wheel_binary(self):
        with patch.object(app_mod, "_dev_binary", return_value=None), \
             patch.object(app_mod, "_bundled_binary", return_value="/package/_bin/OzmApp"), \
             patch.object(app_mod, "_install_app", return_value="/apps/ozm.app") as install, \
             patch.object(app_mod, "_launch_detached") as launch:
            result = CliRunner().invoke(app_cmd, ["start"])
        self.assertEqual(result.exit_code, 0, result.output)
        install.assert_called_once_with("/package/_bin/OzmApp")
        launch.assert_called_once_with(["/usr/bin/open", "-g", "/apps/ozm.app"])

    def test_start_uses_an_existing_bundle_when_no_binary_is_available(self):
        with patch.object(app_mod, "_dev_binary", return_value=None), \
             patch.object(app_mod, "_bundled_binary", return_value=None), \
             patch.object(app_mod, "_app_path", return_value="/apps/ozm.app"), \
             patch.object(app_mod.os.path, "isdir", return_value=True), \
             patch.object(app_mod, "_install_app") as install, \
             patch.object(app_mod, "_launch_detached") as launch:
            result = CliRunner().invoke(app_cmd, ["start"])
        self.assertEqual(result.exit_code, 0, result.output)
        install.assert_not_called()
        launch.assert_called_once_with(["/usr/bin/open", "-g", "/apps/ozm.app"])

    def test_missing_app_does_not_launch_anything(self):
        with patch.object(app_mod, "_dev_binary", return_value=None), \
             patch.object(app_mod, "_bundled_binary", return_value=None), \
             patch.object(app_mod.os.path, "isdir", return_value=False), \
             patch.object(app_mod, "_launch_detached") as launch:
            result = CliRunner().invoke(app_cmd, ["start"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("app not found", result.output)
        launch.assert_not_called()

    def test_install_or_signing_failure_does_not_launch_an_unprepared_app(self):
        for error in (
            OSError("copy failed"),
            subprocess.CalledProcessError(1, ["codesign"], stderr="signing failed"),
        ):
            with self.subTest(error=error), \
                 patch.object(app_mod, "_dev_binary", return_value="/build/OzmApp"), \
                 patch.object(app_mod, "_install_app", side_effect=error), \
                 patch.object(app_mod, "_launch_detached") as launch:
                result = CliRunner().invoke(app_cmd, ["start"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("could not prepare app bundle", result.output)
            launch.assert_not_called()


class AppBundleTests(unittest.TestCase):
    def test_installs_and_updates_a_signed_menu_bar_bundle(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            source = os.path.abspath("OzmApp")
            bundle = os.path.abspath("Applications/ozm.app")
            with open(source, "wb") as file:
                file.write(b"first executable")
            os.chmod(source, 0o755)
            with patch.object(app_mod, "_app_path", return_value=bundle), \
                 patch.object(app_mod.subprocess, "run") as sign:
                self.assertEqual(app_mod._install_app(source), bundle)
                executable = os.path.join(bundle, "Contents", "MacOS", "OzmApp")
                with open(executable, "rb") as file:
                    self.assertEqual(file.read(), b"first executable")
                self.assertTrue(os.access(executable, os.X_OK))
                with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as file:
                    info = plistlib.load(file)
                self.assertEqual(info["CFBundleIdentifier"], "com.kamyar.ozm")
                self.assertEqual(info["CFBundleExecutable"], "OzmApp")
                self.assertEqual(info["CFBundlePackageType"], "APPL")
                self.assertTrue(info["LSUIElement"])
                sign.assert_called_once_with(
                    ["/usr/bin/codesign", "--force", "--sign", "-", ANY],
                    check=True, capture_output=True, text=True,
                )
                signed_path = sign.call_args.args[0][-1]
                self.assertNotEqual(signed_path, bundle)
                self.assertEqual(os.path.basename(signed_path), "ozm.app")
                self.assertFalse(os.path.exists(os.path.dirname(signed_path)))
                with open(source, "wb") as file:
                    file.write(b"updated executable")
                app_mod._install_app(source)
                with open(executable, "rb") as file:
                    self.assertEqual(file.read(), b"updated executable")
                self.assertEqual(os.listdir(os.path.dirname(executable)), ["OzmApp"])

    def test_failed_signing_preserves_the_complete_previous_bundle(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            source = os.path.abspath("OzmApp")
            bundle = os.path.abspath("Applications/ozm.app")
            with open(source, "wb") as file:
                file.write(b"working executable")
            with patch.object(app_mod, "_app_path", return_value=bundle), \
                 patch.object(app_mod.subprocess, "run") as sign:
                app_mod._install_app(source)
                executable = os.path.join(bundle, "Contents", "MacOS", "OzmApp")
                info_path = os.path.join(bundle, "Contents", "Info.plist")
                with open(info_path, "rb") as file:
                    previous_info = file.read()
                previous_inode = os.stat(bundle).st_ino
                with open(source, "wb") as file:
                    file.write(b"invalid replacement")
                sign.side_effect = subprocess.CalledProcessError(1, ["codesign"], stderr="invalid binary")
                with self.assertRaises(subprocess.CalledProcessError):
                    app_mod._install_app(source)
                with open(executable, "rb") as file:
                    self.assertEqual(file.read(), b"working executable")
                with open(info_path, "rb") as file:
                    self.assertEqual(file.read(), previous_info)
                self.assertEqual(os.stat(bundle).st_ino, previous_inode)
                self.assertEqual(os.listdir(os.path.dirname(bundle)), ["ozm.app"])

    def test_publish_failure_restores_the_previous_bundle(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            source = os.path.abspath("OzmApp")
            bundle = os.path.abspath("Applications/ozm.app")
            with open(source, "wb") as file:
                file.write(b"working executable")
            with patch.object(app_mod, "_app_path", return_value=bundle), \
                 patch.object(app_mod.subprocess, "run"):
                app_mod._install_app(source)
                previous_inode = os.stat(bundle).st_ino
                with open(source, "wb") as file:
                    file.write(b"replacement")
                replace = os.replace

                def fail_publish(src, dst):
                    if dst == bundle and os.path.basename(src) == "ozm.app":
                        raise OSError("publish failed")
                    return replace(src, dst)

                with patch.object(app_mod.os, "replace", side_effect=fail_publish):
                    with self.assertRaisesRegex(OSError, "publish failed"):
                        app_mod._install_app(source)
                with open(os.path.join(bundle, "Contents", "MacOS", "OzmApp"), "rb") as file:
                    self.assertEqual(file.read(), b"working executable")
                self.assertEqual(os.stat(bundle).st_ino, previous_inode)
                self.assertEqual(os.listdir(os.path.dirname(bundle)), ["ozm.app"])

    def test_failed_rollback_keeps_the_recovery_copy(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            source = os.path.abspath("OzmApp")
            bundle = os.path.abspath("Applications/ozm.app")
            with open(source, "wb") as file:
                file.write(b"working executable")
            with patch.object(app_mod, "_app_path", return_value=bundle), \
                 patch.object(app_mod.subprocess, "run"):
                app_mod._install_app(source)
                replace = os.replace

                def fail_destination(src, dst):
                    if dst == bundle:
                        raise OSError("destination unavailable")
                    return replace(src, dst)

                with patch.object(app_mod.os, "replace", side_effect=fail_destination):
                    with self.assertRaisesRegex(OSError, "previous bundle preserved at"):
                        app_mod._install_app(source)
                roots = os.listdir(os.path.dirname(bundle))
                self.assertEqual(len(roots), 1)
                backup = os.path.join(os.path.dirname(bundle), roots[0], "previous.app")
                with open(os.path.join(backup, "Contents", "MacOS", "OzmApp"), "rb") as file:
                    self.assertEqual(file.read(), b"working executable")

    def test_failed_copy_preserves_the_previous_executable(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            bundle = os.path.abspath("ozm.app")
            macos = os.path.join(bundle, "Contents", "MacOS")
            os.makedirs(macos)
            executable = os.path.join(macos, "OzmApp")
            with open(executable, "wb") as file:
                file.write(b"previous executable")
            with patch.object(app_mod, "_app_path", return_value=bundle), \
                 patch.object(app_mod.shutil, "copy2", side_effect=OSError("copy failed")), \
                 patch.object(app_mod.subprocess, "run") as sign:
                with self.assertRaises(OSError):
                    app_mod._install_app("/build/OzmApp")
            with open(executable, "rb") as file:
                self.assertEqual(file.read(), b"previous executable")
            self.assertEqual(os.listdir(macos), ["OzmApp"])
            sign.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm.app import app_cmd


class AppStartTests(unittest.TestCase):
    @patch("ozm.app.subprocess.Popen")
    @patch("ozm.app._dev_binary", return_value="/tmp/OzmApp")
    def test_start_detaches_binary_from_invoking_terminal(self, _dev_binary, popen):
        result = CliRunner().invoke(app_cmd, ["start"])

        self.assertEqual(result.exit_code, 0, result.output)
        popen.assert_called_once_with(
            ["/tmp/OzmApp"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertIn("ozm: launched /tmp/OzmApp", result.output)


if __name__ == "__main__":
    unittest.main()

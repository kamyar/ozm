import os
import stat
import unittest
from unittest.mock import patch

from ozm import approve as approve_mod


class ApprovalTemporaryFileTests(unittest.TestCase):
    def test_secure_tmpfile_avoids_mktemp_and_writes_private_file(self):
        content = "review this\nbefore approving"

        with patch.object(
            approve_mod.tempfile,
            "mktemp",
            side_effect=AssertionError("tempfile.mktemp must not be used"),
        ):
            path = approve_mod._secure_tmpfile(".review", content)

        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        self.assertTrue(path.endswith(".review"))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertEqual(f.read(), content)


if __name__ == "__main__":
    unittest.main()

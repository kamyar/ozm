#!/usr/bin/env python3

import json
import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import rule_packs as packs_mod


VALID_PACK = {
    "version": 1,
    "description": "Private work tools.",
    "entrypoint_redirects": [
        {
            "id": "example-inspect-entrypoint",
            "target": "example-inspect",
            "python_modules": ["example_inspect"],
            "script_basenames": ["example-inspect.py"],
            "guidance": "Invoke the installed entry point directly.",
        }
    ],
}


class RulePackLoadingTests(unittest.TestCase):
    def load_from(self, root, project_config=None, global_config=None):
        with patch.object(packs_mod, "OZM_DIR", root), \
             patch.object(
                 packs_mod,
                 "RULE_PACKS_DIR",
                 os.path.join(root, "rule-packs"),
             ), patch.object(
                 packs_mod,
                 "load_project_config",
                 return_value=project_config or {},
             ), patch.object(
                 packs_mod,
                 "load_global_config",
                 return_value=global_config or {},
             ):
            return packs_mod.load_entrypoint_redirects()

    def write_pack(self, root, name="work", payload=None):
        directory = os.path.join(root, "rule-packs")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{name}.yaml")
        with open(path, "w") as file:
            json.dump(VALID_PACK if payload is None else payload, file)
        return path

    def test_loads_explicit_private_pack(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("private")
            self.write_pack(root)
            redirects = self.load_from(
                root,
                global_config={"rule_packs": ["work"]},
            )

        self.assertEqual(len(redirects), 1)
        self.assertEqual(redirects[0].source, "work")
        self.assertEqual(redirects[0].target, "example-inspect")
        self.assertEqual(redirects[0].python_modules, ("example_inspect",))

    def test_project_and_global_pack_names_are_deduplicated(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("private")
            self.write_pack(root)
            redirects = self.load_from(
                root,
                project_config={"rule_packs": ["work"]},
                global_config={"rule_packs": ["work"]},
            )

        self.assertEqual(len(redirects), 1)

    def test_no_enabled_pack_needs_no_pack_directory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("private")
            os.makedirs(root)
            redirects = self.load_from(root)
        self.assertEqual(redirects, ())

    def test_invalid_or_missing_pack_fails_closed(self):
        bad_payloads = [
            {},
            {"version": 2, "entrypoint_redirects": []},
            {"version": 1, "unknown": []},
            {"version": 1, "entrypoint_redirects": [{}]},
            {
                "version": 1,
                "entrypoint_redirects": [{
                    "id": "bad rule id",
                    "target": "example-inspect",
                    "python_modules": ["example_inspect"],
                }],
            },
            {
                "version": 1,
                "entrypoint_redirects": [{
                    "id": "missing-matchers",
                    "target": "example-inspect",
                }],
            },
        ]
        runner = CliRunner()
        for payload in bad_payloads:
            with self.subTest(payload=payload), runner.isolated_filesystem():
                root = os.path.abspath("private")
                self.write_pack(root, payload=payload)
                with self.assertRaises(RuntimeError):
                    self.load_from(
                        root,
                        global_config={"rule_packs": ["work"]},
                    )

    def test_pack_name_cannot_escape_private_directory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("private")
            os.makedirs(root)
            with self.assertRaises(RuntimeError):
                self.load_from(
                    root,
                    global_config={"rule_packs": ["../outside"]},
                )

    def test_symlinked_pack_is_rejected(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = os.path.abspath("private")
            os.makedirs(os.path.join(root, "rule-packs"))
            with open("outside.yaml", "w") as file:
                json.dump(VALID_PACK, file)
            os.symlink(
                os.path.abspath("outside.yaml"),
                os.path.join(root, "rule-packs", "work.yaml"),
            )
            with self.assertRaises(RuntimeError):
                self.load_from(
                    root,
                    global_config={"rule_packs": ["work"]},
                )


if __name__ == "__main__":
    unittest.main()

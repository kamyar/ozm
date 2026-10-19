#!/usr/bin/env python3

import hashlib
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import agent as agent_mod, audit as audit_mod, cmd as cmd_mod, config as config_mod
from ozm import run as run_mod, shell as shell_mod
from ozm.audit_shell import describe_shell
from ozm.approve import ApprovalResult
from ozm.rule_packs import EntrypointRedirectRule

META = ["--agent-name", "Audit tests", "--agent-description", "This description must not enter audit context."]


class AuditShellTests(unittest.TestCase):
    def test_substitution_reports_commands_not_arguments(self):
        text = 'RESULT=$(example-inspect query token=secret-value) && jq \'.values\' "$RESULT"'
        with patch("subprocess.run") as execution:
            description = describe_shell(text)
        self.assertEqual(description["families"], ["example-inspect", "jq"])
        self.assertEqual(description["nested_families"], ["example-inspect"])
        self.assertEqual(description["classification"], "static")
        self.assertNotIn("secret-value", json.dumps(description))
        execution.assert_not_called()

    def test_substitution_comment_boundaries_are_unknown(self):
        cases = [
            "RESULT=$(printf ok # ) ; private-token; printf '\n)\n# '\nprintf done",
            "RESULT=$(printf ok # ) ; private-token\n) && printf done",
        ]
        for text in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "unknown")
                self.assertNotIn("private-token", json.dumps(description))

    def test_quoted_or_escaped_hashes_in_substitutions_are_supported(self):
        cases = [
            "RESULT=$(printf '# ) private-token') && jq .",
            'RESULT=$(printf "# ) private-token") && jq .',
            r"RESULT=$(printf \# private-token) && jq .",
        ]
        for text in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "static")
                self.assertEqual(description["families"], ["printf", "jq"])
                self.assertNotIn("private-token", json.dumps(description))

    def test_extended_quote_syntax_is_unknown(self):
        cases = [
            "printf $'private-token'", 'printf $"private-token"',
            "RESULT=$(printf $'private-token') && jq .",
            'RESULT=$(printf $"private-token") && jq .',
            "NAME=$'private-token' printf done",
        ]
        for text in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "unknown")
                self.assertNotIn("private-token", json.dumps(description))

    def test_arithmetic_expansions_are_unknown_without_argument_values(self):
        cases = [
            "printf '%s' $[1 | 987654321 ]",
            'printf "%s" "$[1 | 987654321 ]"',
            "VALUE=$[1 | 987654321 ]",
            "printf '%s' before$[1 | 987654321 ]after",
            "RESULT=$(printf '%s' $[1 | 987654321 ]) && jq .",
            "RESULT=$(outer value=$(printf '%s' $[1 | 987654321 ])) && jq .",
            "printf '%s' $((1 | 987654321))",
        ]
        for text in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "unknown")
                self.assertEqual(description["families"], ["unknown"])
                self.assertEqual(description["nested_families"], [])
                self.assertNotIn("987654321", json.dumps(description))

    def test_literal_arithmetic_syntax_is_not_expanded(self):
        cases = [
            ("printf '%s' '$[1 | 987654321 ]'", ["printf"]),
            (r"printf '%s' \$[1 \| 987654321 ]", ["printf"]),
            ('printf "%s" "\\$[1 | 987654321 ]"', ["printf"]),
            ("VALUE='$[1 | 987654321 ]'", ["assignment"]),
            ("RESULT=$(printf '%s' '$[1 | 987654321 ]') && jq .", ["printf", "jq"]),
        ]
        for text, families in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "static")
                self.assertEqual(description["families"], families)
                self.assertNotIn("987654321", json.dumps(description))

    def test_quotes_and_escaped_substitutions_are_not_commands(self):
        cases = [
            ("printf '%s' '$(fake-command secret)'", ["printf"]),
            ('printf "%s" "\\$(fake-command secret)"', ["printf"]),
            ("RESULT='$(fake-command secret)'", ["assignment"]),
            ("printf '%s' '&& fake-command | other'", ["printf"]),
            ("# $(fake-command)\nprintf ok", ["printf"]),
        ]
        for text, families in cases:
            with self.subTest(text=text):
                result = describe_shell(text)
                self.assertEqual(result["families"], families)
                self.assertEqual(result["nested_families"], [])

    def test_nested_substitutions_and_pipelines(self):
        description = describe_shell('RESULT="$(outer key="$(inner value)")" && jq . "$RESULT" | sort')
        self.assertEqual(set(description["families"]), {"inner", "outer", "jq", "sort"})
        self.assertEqual(set(description["nested_families"]), {"inner", "outer"})

    def test_assignment_prefix_must_be_literal_and_unquoted(self):
        cases = [
            "'NAME=value' private-token", '"NAME=value" private-token',
            "'NAME'=value private-token", 'NAME""=value private-token',
            r"N\AME=value private-token", r"NAME\=value private-token",
            "$NAME=value private-token", "NAME$SUFFIX=value private-token",
            "$(printf NAME)=value private-token", "NAME$(printf suffix)=value private-token",
            "RESULT=$('NAME=value' private-token) && printf done",
        ]
        for text in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "unknown")
                self.assertNotIn("private-token", json.dumps(description))

    def test_quotes_and_expansions_in_assignment_values_are_supported(self):
        cases = [
            ("NAME='private-token' printf ok", ["printf"]),
            ('NAME="private-token" printf ok', ["printf"]),
            (r"NAME=private\ token printf ok", ["printf"]),
            ("NAME=$VALUE printf ok", ["printf"]),
            ('NAME="$(printf private-token)" jq .', ["printf", "jq"]),
            ("NAME=value\u00a0private-token", ["assignment"]),
            ("NAME=value\rprivate-token", ["assignment"]),
        ]
        for text, families in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "static")
                self.assertEqual(description["families"], families)
                self.assertNotIn("private-token", json.dumps(description))

    def test_quoted_or_escaped_descriptor_words_are_not_removed(self):
        for prefix in ["'2'", '"2"', r"\2", '2""']:
            with self.subTest(prefix=prefix):
                description = describe_shell(f"{prefix}>output private-token")
                self.assertEqual(description["families"], ["2"])
                self.assertNotIn("private-token", json.dumps(description))

    def test_unicode_digits_are_not_file_descriptors(self):
        for digits in ["\u00b2", "\u0662", "\uff12"]:
            for text in [
                f"{digits}>output private-token",
                f"RESULT=$({digits}>output private-token) && printf done",
            ]:
                with self.subTest(text=text):
                    description = describe_shell(text)
                    self.assertEqual(description["classification"], "unknown")
                    self.assertNotIn("private-token", json.dumps(description))

    def test_out_of_range_numeric_descriptors_are_unknown(self):
        for digits in ["2147483648", "99999999999", "0" * 100]:
            for text in [
                f"{digits}>output private-token",
                f"RESULT=$({digits}>output private-token) && printf done",
            ]:
                with self.subTest(text=text):
                    description = describe_shell(text)
                    self.assertEqual(description["classification"], "unknown")
                    self.assertNotIn("private-token", json.dumps(description))
        description = describe_shell("2147483647>output printf private-token")
        self.assertEqual(description["classification"], "static")
        self.assertEqual(description["families"], ["printf"])
        self.assertNotIn("private-token", json.dumps(description))

    def test_malformed_command_lists_are_unknown(self):
        cases = [
            "printf &&", "printf ||", "printf |", "| printf",
            "&& printf", "printf || || private-token", "printf ; ; private-token",
            "printf ; & private-token", "printf && ; private-token",
            "printf &&\n|| private-token", "printf && # comment\n",
            "printf\n&& private-token", "printf > > private-token",
        ]
        for text in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "unknown")
                self.assertNotIn("private-token", json.dumps(description))

    def test_valid_command_list_endings_and_operand_newlines(self):
        cases = [
            ("printf ok;", ["printf"]), ("printf ok &", ["printf"]),
            ("\n\nprintf ok\n\n", ["printf"]),
            ("printf ok &&\n\njq .", ["printf", "jq"]),
            ("printf ok | # comment\n sort", ["printf", "sort"]),
            ("printf ok ||\nNAME=value", ["printf"]),
        ]
        for text, families in cases:
            with self.subTest(text=text):
                description = describe_shell(text)
                self.assertEqual(description["classification"], "static")
                self.assertEqual(description["families"], families)

    def test_unsupported_or_unbounded_input_is_unknown(self):
        cases = [
            "if test yes; then example-inspect; fi", "eval hidden-command",
            "${EXECUTABLE} input", "$EXECUTABLE input", "printf `hidden-command`",
            "printf 'unterminated", "cat <<EOF\nfake-command\nEOF",
            "printf $((1 + 2))", "x" * 100_001,
            "$(" * 10 + "printf ok" + ")" * 10,
            "'' fake-command", '"" fake-command', "2>/tmp/output",
            "printf &&&& fake-command", "printf > && fake-command",
        ]
        for text in cases:
            with self.subTest(text=text[:100]):
                self.assertEqual(describe_shell(text)["classification"], "unknown")


class AuditContextTests(unittest.TestCase):
    def _paths(self):
        directory = os.path.abspath("audit")
        return directory, os.path.join(directory, "audit.log")

    def test_config_rule_identity_and_agent_are_recorded_without_description(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(config_mod, "load_project_config", return_value={}), \
                 patch.object(config_mod, "load_global_config", return_value={"allowed_commands": ["example-inspect status *"]}), \
                 patch.object(cmd_mod, "_run_command", return_value=subprocess.CompletedProcess([], 0)):
                result = runner.invoke(cmd_mod.cmd_cmd, [*META, "example-inspect", "status", "secret-value"])
                self.assertEqual(result.exit_code, 0, result.output)
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(entry["agent"], "Audit tests")
        self.assertEqual(entry["rule"], "global.allowed_commands[0]")
        self.assertEqual(entry["decision_reason"], "command permission")
        self.assertEqual(entry["command_family"], "example-inspect")
        details = entry["raw"].split(audit_mod._DETAIL_MARKER, 1)[1]
        self.assertNotIn("secret-value", details)
        self.assertNotIn(META[3], details)

    def test_invalid_metadata_records_category_not_values(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(cmd_mod, "_run_command") as execution:
                result = runner.invoke(cmd_mod.cmd_cmd, ["--agent-json", "secret invalid json", "example-inspect", "secret-argument"])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, agent_mod.MISSING_METADATA)
        self.assertEqual(entry["action"], "invalid")
        self.assertEqual(entry["failure_category"], "invalid_metadata_json")
        self.assertEqual(entry["target"], "<metadata>")
        self.assertNotIn("secret", entry["raw"])
        execution.assert_not_called()

    def test_validation_audit_failure_preserves_original_exit_code(self):
        with patch.object(agent_mod, "audit_log", side_effect=OSError("disk full")), \
             patch.object(cmd_mod, "_run_command") as execution:
            result = CliRunner().invoke(cmd_mod.cmd_cmd, ["example-inspect"])
        self.assertEqual(result.exit_code, agent_mod.MISSING_METADATA)
        execution.assert_not_called()

    def test_generated_audit_records_nested_families_and_content_digest(self):
        runner = CliRunner()
        text = 'RESULT=$(example-inspect query token=secret-value) && jq . "$RESULT"'
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)):
                result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, run_mod.DENIED)
        self.assertEqual(entry["families"], ["example-inspect", "jq"])
        self.assertEqual(entry["nested_families"], ["example-inspect"])
        self.assertEqual(entry["script_sha256"], hashlib.sha256(shell_mod._script_for_bash(text).encode()).hexdigest())
        self.assertNotIn("secret-value", entry["raw"])
        self.assertNotIn(META[3], entry["raw"])

    def test_arithmetic_argument_values_do_not_enter_generated_audit(self):
        runner = CliRunner()
        cases = [
            "printf '%s' $[1 | 987654321 ]",
            "RESULT=$(printf '%s' $[1 | 987654321 ]) && jq .",
        ]
        for text in cases:
            with self.subTest(text=text), runner.isolated_filesystem():
                directory, file = self._paths()
                with patch.object(audit_mod, "OZM_DIR", directory), \
                     patch.object(audit_mod, "AUDIT_FILE", file), \
                     patch.object(run_mod, "load_entrypoint_redirects", return_value=()), \
                     patch.object(run_mod, "load_hashes", return_value={}), \
                     patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)) as approval, \
                     patch.object(run_mod, "_execute_script") as execution:
                    result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                    with open(file) as stream:
                        entry = audit_mod.parse_line(stream.readline())
                self.assertEqual(result.exit_code, run_mod.DENIED, result.output)
                self.assertEqual(entry["action"], "denied")
                self.assertEqual(entry["classification"], "unknown")
                self.assertEqual(entry["families"], ["unknown"])
                self.assertEqual(entry["nested_families"], [])
                self.assertNotIn("987654321", entry["raw"])
                approval.assert_called_once()
                execution.assert_not_called()

    def test_ambiguous_assignment_does_not_enter_generated_audit_fields(self):
        runner = CliRunner()
        text = "RESULT=$('NAME=value' private-token) && printf done"
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(run_mod, "load_entrypoint_redirects", return_value=()), \
                 patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)):
                result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, run_mod.DENIED, result.output)
        self.assertEqual(entry["families"], ["unknown"])
        self.assertEqual(entry["nested_families"], [])
        self.assertNotIn("private-token", entry["raw"])

    def test_substitution_comment_text_does_not_enter_generated_audit(self):
        runner = CliRunner()
        text = "RESULT=$(printf ok # ) ; private-token; printf '\n)\n# '\nprintf done"
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(run_mod, "load_entrypoint_redirects", return_value=()), \
                 patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)):
                result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, run_mod.DENIED, result.output)
        self.assertEqual(entry["classification"], "unknown")
        self.assertEqual(entry["families"], ["unknown"])
        self.assertEqual(entry["nested_families"], [])
        self.assertNotIn("private-token", entry["raw"])

    def test_non_shell_generated_shebang_uses_unknown_without_parsing(self):
        runner = CliRunner()
        text = "#!/usr/bin/env python3\n'first-private-value'\n'second-private-value'\n"
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(run_mod, "load_entrypoint_redirects", return_value=()), \
                 patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)) as approval, \
                 patch.object(run_mod, "describe_shell") as parser:
                result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, run_mod.BLOCKED, result.output)
        self.assertEqual(entry["classification"], "unknown")
        self.assertEqual(entry["families"], ["unknown"])
        self.assertEqual(entry["nested_families"], [])
        self.assertNotIn("private-value", entry["raw"])
        parser.assert_not_called()
        approval.assert_not_called()

    def test_non_shell_line_separators_remain_arguments_in_generated_audit(self):
        runner = CliRunner()
        for separator in ["\u2028", "\u2029", "\u0085", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e"]:
            text = f"printf ok{separator}private-token"
            with self.subTest(separator=repr(separator)), runner.isolated_filesystem():
                directory, file = self._paths()
                with patch.object(audit_mod, "OZM_DIR", directory), \
                     patch.object(audit_mod, "AUDIT_FILE", file), \
                     patch.object(run_mod, "load_entrypoint_redirects", return_value=()), \
                     patch.object(run_mod, "load_hashes", return_value={}), \
                     patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)), \
                     patch.object(run_mod, "describe_shell", wraps=describe_shell) as parser:
                    result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                    with open(file) as stream:
                        entry = audit_mod.parse_line(stream.readline())
                self.assertEqual(result.exit_code, run_mod.BLOCKED, result.output)
                self.assertEqual(entry["classification"], "static")
                self.assertEqual(entry["families"], ["printf"])
                self.assertNotIn("private-token", entry["raw"])
                parser.assert_called_once_with(text + "\n")

    def test_non_ascii_shebang_delimiters_are_unknown(self):
        runner = CliRunner()
        for shebang in ["#!/bin/bash\u2028", "#!/bin/bash\r", "#!/usr/bin/env\u00a0bash"]:
            text = shebang + "\n'first-private-value'\n'second-private-value'\n"
            with self.subTest(shebang=shebang), runner.isolated_filesystem():
                directory, file = self._paths()
                with patch.object(audit_mod, "OZM_DIR", directory), \
                     patch.object(audit_mod, "AUDIT_FILE", file), \
                     patch.object(run_mod, "load_entrypoint_redirects", return_value=()), \
                     patch.object(run_mod, "load_hashes", return_value={}), \
                     patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)), \
                     patch.object(run_mod, "describe_shell") as parser:
                    result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                    with open(file) as stream:
                        entry = audit_mod.parse_line(stream.readline())
                self.assertEqual(result.exit_code, run_mod.BLOCKED, result.output)
                self.assertEqual(entry["classification"], "unknown")
                self.assertEqual(entry["families"], ["unknown"])
                self.assertNotIn("private-value", entry["raw"])
                parser.assert_not_called()

    def test_generated_local_rule_block_records_structured_identifier(self):
        runner = CliRunner()
        rule = EntrypointRedirectRule(
            source="sample", rule_id="installed-entrypoint", target="example-inspect",
            python_modules=("example_inspect",), script_basenames=(),
            guidance="Invoke the installed entry point directly.",
        )
        text = "python3 -m example_inspect status\nprintf done"
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(run_mod, "load_entrypoint_redirects", return_value=(rule,)), \
                 patch.object(run_mod, "request_approval") as approval:
                result = runner.invoke(shell_mod.shell_cmd, ["--command", text, *META])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, run_mod.BLOCKED, result.output)
        self.assertEqual(entry["action"], "blocked")
        self.assertEqual(entry["rule"], "local:sample/installed-entrypoint")
        approval.assert_not_called()

    def test_persistent_script_records_digest_without_body(self):
        runner = CliRunner()
        content = "#!/usr/bin/env sh\nprintf secret-value\nprintf done\n"
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with open("script.sh", "w") as stream:
                stream.write(content)
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(run_mod, "load_hashes", return_value={}), \
                 patch.object(run_mod, "request_approval", return_value=ApprovalResult(False)):
                result = runner.invoke(run_mod.run_cmd, [*META, "script.sh"])
                with open(file) as stream:
                    entry = audit_mod.parse_line(stream.readline())
        self.assertEqual(result.exit_code, run_mod.DENIED)
        self.assertEqual(entry["script_sha256"], hashlib.sha256(content.encode()).hexdigest())
        self.assertEqual(entry["source"], "file")
        self.assertNotIn("secret-value", entry["raw"])

    def test_context_does_not_leak_between_invocations(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            directory, file = self._paths()
            with patch.object(audit_mod, "OZM_DIR", directory), \
                 patch.object(audit_mod, "AUDIT_FILE", file), \
                 patch.object(cmd_mod, "is_command_blocked", return_value=None), \
                 patch.object(cmd_mod, "_run_command", return_value=subprocess.CompletedProcess([], 0)):
                runner.invoke(cmd_mod.cmd_cmd, [*META, "command", "-v", "example-inspect"])
                runner.invoke(cmd_mod.cmd_cmd, ["--agent-json", "invalid-json", "example-inspect"])
                with open(file) as stream:
                    entries = [audit_mod.parse_line(line) for line in stream]
        self.assertEqual(entries[0]["agent"], "Audit tests")
        self.assertNotIn("agent", entries[1])
        self.assertNotIn("rule", entries[1])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

import subprocess
import shlex
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ozm import cmd as cmd_mod
from ozm.approve import ApprovalResult


META = [
    "--agent-name", "GitHub GraphQL test",
    "--agent-description", "Exercise read-only GitHub GraphQL auto-allow behavior.",
]


PEDREGAL_READ_QUERY = """
{
  repository(owner: "doordash", name: "pedregal") {
    ddMirror: issue(number: 146130) {
      title
      subIssues(first: 30) {
        nodes {
          number
          title
          state
          assignees(first: 3) { nodes { login } }
        }
      }
    }
    mirrorToLegacy: issue(number: 163960) {
      title
      subIssues(first: 30) {
        nodes {
          number
          title
          state
          assignees(first: 3) { nodes { login } }
        }
      }
    }
  }
}
""".strip()


class GitHubGraphQLReadAutoAllowTests(unittest.TestCase):
    def run_cmd(self, args, *, blocked=None, approval=None):
        completed = subprocess.CompletedProcess(args=args, returncode=0)
        approval = approval or ApprovalResult(approved=False)
        patches = [
            patch.object(cmd_mod, "is_command_blocked", return_value=blocked),
            patch.object(cmd_mod, "is_command_allowed", return_value=False),
            patch.object(cmd_mod, "load_hashes", return_value={}),
            patch.object(cmd_mod, "request_cmd_approval", return_value=approval),
            patch.object(cmd_mod, "_run_command", return_value=completed),
            patch.object(cmd_mod, "audit_log"),
        ]
        with patches[0] as is_blocked, \
            patches[1] as is_allowed, \
            patches[2] as load_hashes, \
            patches[3] as request_approval, \
            patches[4] as run_command, \
            patches[5] as audit_log:
            result = CliRunner().invoke(cmd_mod.cmd_cmd, [*META, *args])
        return result, is_blocked, is_allowed, load_hashes, request_approval, run_command, audit_log

    def test_shorthand_github_graphql_query_runs_without_approval(self):
        args = ["gh", "api", "graphql", "-f", f"query={PEDREGAL_READ_QUERY}"]

        result, _blocked, _allowed, load_hashes, request_approval, run_command, audit_log = self.run_cmd(args)

        self.assertEqual(result.exit_code, 0, result.output)
        request_approval.assert_not_called()
        load_hashes.assert_not_called()
        run_command.assert_called_once_with(args)
        audit_log.assert_called_once_with("semantic", "cmd", shlex.join(args), "github graphql query")
        self.assertIn("allowed (github graphql query)", result.output)

    def test_named_github_graphql_query_with_operation_name_runs_without_approval(self):
        document = """
query ReadIssue {
  repository(owner: "doordash", name: "pedregal") {
    issue(number: 146130) { title }
  }
}

mutation UpdateIssue {
  addComment(input: { subjectId: "I_kwDOAA", body: "x" }) { clientMutationId }
}
""".strip()
        args = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={document}",
            "-f",
            "operationName=ReadIssue",
        ]

        result, _blocked, _allowed, load_hashes, request_approval, run_command, _audit_log = self.run_cmd(args)

        self.assertEqual(result.exit_code, 0, result.output)
        request_approval.assert_not_called()
        load_hashes.assert_not_called()
        run_command.assert_called_once_with(args)

    def test_github_graphql_mutation_still_requires_approval(self):
        mutation = """
mutation AddComment {
  addComment(input: { subjectId: "I_kwDOAA", body: "x" }) { clientMutationId }
}
""".strip()
        args = ["gh", "api", "graphql", "-f", f"query={mutation}"]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(args)

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_called_once()
        run_command.assert_not_called()
        self.assertIn("denied cmd", result.output)

    def test_multiple_github_graphql_operations_without_operation_name_require_approval(self):
        document = """
query ReadIssue {
  repository(owner: "doordash", name: "pedregal") {
    issue(number: 146130) { title }
  }
}

query ReadViewer {
  viewer { login }
}
""".strip()
        args = ["gh", "api", "graphql", "-f", f"query={document}"]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(args)

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_called_once()
        run_command.assert_not_called()

    def test_file_backed_github_graphql_query_requires_approval(self):
        args = ["gh", "api", "graphql", "-f", "query=@query.graphql"]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(args)

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_called_once()
        run_command.assert_not_called()

    def test_malformed_github_graphql_query_requires_approval(self):
        args = ["gh", "api", "graphql", "-f", "query={ viewer { login }"]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(args)

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_called_once()
        run_command.assert_not_called()

    def test_blocklist_wins_over_read_only_github_graphql_auto_allow(self):
        args = ["gh", "api", "graphql", "-f", f"query={PEDREGAL_READ_QUERY}"]

        result, _blocked, _allowed, _load_hashes, request_approval, run_command, _audit_log = self.run_cmd(
            args,
            blocked="gh api graphql *",
        )

        self.assertNotEqual(result.exit_code, 0)
        request_approval.assert_not_called()
        run_command.assert_not_called()
        self.assertIn("blocked by pattern", result.output)


if __name__ == "__main__":
    unittest.main()

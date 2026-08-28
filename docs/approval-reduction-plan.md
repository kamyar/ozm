# Recent approval reduction plan

## Goal

Reduce avoidable Ozm approval dialogs without weakening safeguards for local writes, remote writes, downloaded executables, or force pushes.

## Audit baseline

The audit window from 10:00 through 12:55 on 2026-08-28 contained:

- 63 manually approved file runs
- 35 manually approved commands
- 16 manually approved GitHub operations
- 13 Git overrides
- At least 498 automatic command and GitHub decisions

During the 12:00 hour, generated file runs caused 55 of 68 manual approvals. A review of the 20 newest snapshots found that approximately 18 were avoidable shell wrappers.

## Safety principles

1. Keep local and remote writes explicit.
2. Prove read-only behavior before automatic execution.
3. Review script content, not a shell wrapper that invokes mutable script content.
4. Reject agent formatting errors instead of asking the user to override them.
5. Use typed, repository-scoped authorization for routine GitHub writes.
6. Keep downloaded or temporary executables outside normal command approval and cache paths.
7. Preserve enough audit context to explain why a generated file was reviewed.

## Commit topics

### 1. Generated shell wrapper safeguards

- Detect raw generated pipelines that end in supported `head` forms, not only pipelines that start with `ozm`.
- Nudge simple generated command chains to direct, separate Ozm calls.
- Reject generated wrappers that invoke or source another mutable script. Direct the agent to `ozm run` so Ozm reviews the target content.
- Keep real multi-step scripts reviewable.

Validation:

- Raw `rg ... | head -n 20` receives an `ozm --head 20 cmd ...` nudge before approval.
- `cd DIR && bash script.sh` and `source script.sh` do not review only the wrapper.
- Quoted shell syntax does not cause false matches.

### 2. Generic working-directory and output controls

- Add root `--cwd PATH` support so agents do not need `cd ... && ...` wrappers.
- Add root `--tail N` support beside `--grep` and `--head`.
- Apply project policy after changing to the selected working directory.
- Reject incompatible `--head` and `--tail` combinations.

Validation:

- `ozm --cwd WORKTREE git status` uses the worktree project scope.
- `ozm --tail 20 cmd ...` consumes the full child output and preserves the child exit code.

### 3. Proven read-only command classification

Add conservative semantic reads for exact, validated forms of:

- `command -v NAME...`
- `bazel query ...`
- `brew search ...`
- `npm view ...`
- `npm list ...`
- GitHub CLI help requests such as `gh api --help`

Do not auto-allow downloads, installs, lifecycle execution, arbitrary HTTP requests, or unknown forms.

### 4. Script execution routing

- Detect direct `.sh` and other shebang script invocations through `ozm cmd`.
- Redirect them to `ozm run` before command policy, cache, or approval.
- Preserve script arguments and agent metadata in the suggested command.
- Ensure repeated unchanged scripts use the content hash cache.

### 5. Git policy correction

- Make commit-message shape errors non-overridable.
- Reject multiple message sources, embedded newlines, and prohibited attribution with a direct retry instruction.
- Allow force-push override requests only when `--force-with-lease=REF:EXPECTED_SHA` pins both the destination and expected remote state.
- Reject broad `--force`, `--force-with-lease`, and unpinned lease forms without an approval dialog.

### 6. Typed repository-scoped GitHub writes

- Add configuration for `github.allowed_operations` with repository scopes.
- Authorize typed `pr.review-reply` by repository when configured.
- Add typed `issue.add-sub-issue` with fixed endpoint construction and validation.
- Keep PR closes, issue creation, arbitrary PATCH, and unknown writes reviewed.
- Remove broad global GitHub write allowlist patterns after typed policy is available.

### 7. Sensitive command hardening

- Block bare `env` output and direct agents to named-variable inspection.
- Treat executables under temporary, world-writable locations as blocked operations that require a one-time reasoned override.
- Do not let config patterns or command cache entries bypass these checks.

### 8. Audit summaries and generated-run context

- Add `ozm log --summary --since DURATION`.
- Report counts by action and kind, with manual approval totals.
- Add a safe generated-run summary and content digest to run audit entries without logging full script content or secrets.
- Distinguish generated `shell:` and `stdin:` reviews from disk scripts in summaries.

## Local policy migration

After commit topic 6:

1. Remove global write patterns for `gh issue edit`, `gh issue comment`, `gh pr create`, `gh pr edit`, `gh pr comment`, `gh pr ready`, and `gh pr review`.
2. Add repository-scoped authorization only for approved typed operations.
3. Remove broad `pkill` authorization.
4. Keep proven GitHub reads under semantic classification.

## Delivery process

Each topic uses one commit. After every commit:

1. Run focused tests and the full Python test suite.
2. Build the Swift application when app code changes.
3. Push directly to `main` without force.
4. Refresh the editable local Ozm installation and generated hooks.
5. Restart the app only when application code changes.

# ozm — script execution gate

All script execution and git operations must go through `ozm`.

## Rules

- **Always identify the agent work:** every `ozm run`, `ozm cmd`, `ozm gh`, and `ozm git` invocation must include `--agent-name "<what you are working on>"` and `--agent-description "<one-line intent>"`.
- **Run scripts:** `ozm run --agent-name "<work>" --agent-description "<intent>" <script> [args...]` — never `python`, `bash`, `./`, or `uv run` directly
- **Do not wrap direct commands in a script:** `ozm run` rejects scripts with only one executable line. It also rejects disk or in-memory shell files when every command segment invokes `ozm`. Run each Ozm command directly and separately; use `ozm bash --command` only for shell logic that is not a sequence of Ozm commands.
- **Do not chmod scripts for ozm:** `ozm run` executes a private executable snapshot. The source script only needs a shebang. Use `chmod` only when the source file mode itself must change.
- **Run commands:** `ozm cmd --agent-name "<work>" --agent-description "<intent>" <command> [args...]` — for arbitrary commands (e.g. `ozm cmd --agent-name "Install deps" --agent-description "Install editable package dependencies." uv pip install -e .`)
- **Run GitHub commands:** `ozm gh --agent-name "<work>" --agent-description "<intent>" <gh-args...>` — never use direct `gh` or `ozm cmd gh`; proven reads run directly, while writes and unknown operations keep normal approval checks
- **Reply to PR reviews:** use `ozm gh ... pr review-reply --repo OWNER/REPOSITORY --number NUMBER --comment-id ID --body-file FILE` — raw review-reply REST POST requests are blocked
- **Avoid sed:** `sed`/`gsed` are blocked because they can edit files in-place. Use `rg` for searching, `cat`/`nl`/`head`/`tail` for viewing, or `ozm run <script>` for transformations.
- **Avoid curl:** `curl` is blocked by default. Install HTTPie with `uv tool install httpie` and use explicit methods (e.g. `http GET <url>`, `http POST <url> key=value`). For complex requests, write a reviewed Python script using `httpx` (or similar) and run it with `ozm run <script>`.
- **Commit:** `ozm git --agent-name "<work>" --agent-description "<intent>" commit -m "short message"` — max 72 char subject, max 500 chars total
- **Push:** `ozm git --agent-name "<work>" --agent-description "<intent>" push` — no force push, no pushing to main/master
- **Status:** `ozm status` — show tracked scripts
- **Reset:** `ozm reset <script>` or `ozm reset --all`

## Scripts must have a shebang

Always include a shebang line (e.g. `#!/usr/bin/env python3`, `#!/usr/bin/env bash`) as the first line of any script you create. This allows `ozm run` to execute it directly. Never use `ozm cmd python script.py` or `ozm cmd uv run python script.py` — use `ozm run --agent-name "<work>" --agent-description "<intent>" script.py` instead.

Keep commit messages short. No heredoc/EOF patterns. Simple `-m "message"` only.

## Override blocked operations

If a command is blocked but you believe it's necessary, use `--reason` to request a one-time override from the user:

    ozm git --agent-name "Ship hotfix" --agent-description "Push the production fix branch." push --reason "Hotfix for production outage, needs to go to main"
    ozm cmd --agent-name "Clean build" --agent-description "Remove generated build artifacts before rebuild." rm -rf build/ --reason "Clean build artifacts before rebuild"

The user sees your reasoning in a dialog and can approve once. This is never cached or added to allowlists.

If ozm rejects a command because agent metadata is missing or invalid, write the metadata requirement to memory before retrying.

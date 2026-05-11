# ozm — script execution gate

All script execution and git operations must go through `ozm`.

## Rules

- **Always identify the agent work:** every `ozm run`, `ozm cmd`, and `ozm git` invocation must include `--agent-name "<what you are working on>"` and `--agent-description "<one-line intent>"`.
- **Run scripts:** `ozm run --agent-name "<work>" --agent-description "<intent>" <script> [args...]` — never `python`, `bash`, `./`, or `uv run` directly
- **Run commands:** `ozm cmd --agent-name "<work>" --agent-description "<intent>" <command> [args...]` — for arbitrary commands (e.g. `ozm cmd --agent-name "Install deps" --agent-description "Install editable package dependencies." uv pip install -e .`)
- **Avoid sed:** `sed`/`gsed` are blocked because they can edit files in-place. Use `rg` for searching, `cat`/`nl`/`head`/`tail` for viewing, or `ozm run <script>` for transformations.
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

# ozm — script execution gate

All script execution and git operations must go through `ozm`.

## Rules

- **Run scripts:** `ozm run <script> [args...]` — never `python`, `bash`, `./`, or `uv run` directly
- **Run commands:** `ozm cmd <command> [args...]` — for arbitrary commands (e.g. `ozm cmd uv pip install -e .`)
- **Commit:** `ozm git commit -m "short message"` — max 72 char subject, max 500 chars total
- **Push:** `ozm git push` — no force push, no pushing to main/master
- **Status:** `ozm status` — show tracked scripts
- **Reset:** `ozm reset <script>` or `ozm reset --all`

## Scripts must have a shebang

Always include a shebang line (e.g. `#!/usr/bin/env python3`, `#!/usr/bin/env bash`) as the first line of any script you create. This allows `ozm run` to execute it directly. Never use `ozm cmd python script.py` or `ozm cmd uv run python script.py` — use `ozm run script.py` instead.

Keep commit messages short. No heredoc/EOF patterns. Simple `-m "message"` only.

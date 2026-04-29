#!/usr/bin/env python3
"""Install ozm hooks and agent configuration."""

import json
import os
import stat

import click

OZM_DIR = os.path.expanduser("~/.ozm")
HOOKS_DIR = os.path.join(OZM_DIR, "hooks")
ENFORCE_HOOK = os.path.join(HOOKS_DIR, "enforce.sh")

HOOK_SCRIPT = r'''#!/usr/bin/env python3
import json, sys, re

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

command = data.get("tool_input", {}).get("command", "")
if not command:
    sys.exit(0)

def deny(reason):
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.exit(0)

SAFE = {"echo", "printf", "pwd", "date", "true", "false", "test"}
UNSAFE_SHELL = ("$(", "`", "<(", ">", "<")

raw_parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)
stripped_parts = [
    re.sub(r"""(?:"(?:[^"\\]|\\.)*"|'[^']*')""", '""', part)
    for part in raw_parts
]
for raw_part, part in zip(raw_parts, stripped_parts):
    part = part.strip()
    if not part:
        continue
    first_word = re.split(r"\s+", part)[0]
    if first_word == "ozm":
        continue
    if any(token in raw_part for token in UNSAFE_SHELL):
        deny("Use 'ozm cmd ...' for shell substitution, pipes, or redirection.")
    if first_word in SAFE:
        continue
    if first_word == "git":
        deny("Use 'ozm git <subcommand>' instead of 'git' directly.")
    deny(f"Use 'ozm cmd {part}' instead of running commands directly. For script files use 'ozm run <script>'.")
'''

CLAUDE_MD = """# ozm — script execution gate

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

## Override blocked operations

If a command is blocked but you believe it's necessary, use `--reason` to request a one-time override from the user:

    ozm git push --reason "Hotfix for production outage, needs to go to main"
    ozm cmd rm -rf build/ --reason "Clean build artifacts before rebuild"

The user sees your reasoning in a dialog and can approve once. This is never cached or added to allowlists.
"""

AGENTS_MD = """# ozm — script execution gate

All script execution and git operations must go through `ozm`.

## Rules

- **Run scripts:** `ozm run <script> [args...]` — never `python`, `bash`, `./`, or `uv run` directly
- **Run commands:** `ozm cmd <command> [args...]` — for arbitrary commands (e.g. `ozm cmd uv pip install -e .`)
- **Commit:** `ozm git commit -m "short message"` — max 72 char subject, max 500 chars total
- **Push:** `ozm git push` — no force push, no pushing to main/master

## Scripts must have a shebang

Always include a shebang line (e.g. `#!/usr/bin/env python3`, `#!/usr/bin/env bash`) as the first line of any script you create. This allows `ozm run` to execute it directly. Never use `ozm cmd python script.py` or `ozm cmd uv run python script.py` — use `ozm run script.py` instead.

Keep commit messages short. No heredoc/EOF patterns. Simple `-m "message"` only.

## Override blocked operations

If a command is blocked but you believe it's necessary, use `--reason` to request a one-time override from the user:

    ozm git push --reason "Hotfix for production outage, needs to go to main"
    ozm cmd rm -rf build/ --reason "Clean build artifacts before rebuild"

The user sees your reasoning in a dialog and can approve once. This is never cached or added to allowlists.
"""

CLAUDE_HOOKS_CONFIG = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": ENFORCE_HOOK,
                    }
                ],
            }
        ]
    }
}


def _write_hook_script() -> None:
    os.makedirs(HOOKS_DIR, exist_ok=True)
    with open(ENFORCE_HOOK, "w") as f:
        f.write(HOOK_SCRIPT)
    st = os.stat(ENFORCE_HOOK)
    os.chmod(ENFORCE_HOOK, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    click.echo(f"  hook: {ENFORCE_HOOK}")


def _write_file(path: str, content: str) -> None:
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
        if "ozm — script execution gate" in existing:
            click.echo(f"  exists: {path}")
            return
        with open(path, "a") as f:
            f.write("\n\n")
            f.write(content)
        click.echo(f"  appended: {path}")
        return

    with open(path, "w") as f:
        f.write(content)
    click.echo(f"  wrote: {path}")


def _configure_claude_code() -> None:
    claude_dir = os.path.expanduser("~/.claude")
    os.makedirs(claude_dir, exist_ok=True)
    settings_path = os.path.join(claude_dir, "settings.json")

    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
    else:
        settings = {}

    settings.setdefault("hooks", {})
    pre_hooks = settings["hooks"].setdefault("PreToolUse", [])

    already = any(
        h.get("matcher") == "Bash"
        and any(
            hk.get("command") == ENFORCE_HOOK
            for hk in h.get("hooks", [])
        )
        for h in pre_hooks
    )

    if not already:
        pre_hooks.append(CLAUDE_HOOKS_CONFIG["hooks"]["PreToolUse"][0])

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    click.echo(f"  claude: {settings_path}")


@click.command("install")
@click.option("--project", is_flag=True, help="Also write CLAUDE.md and AGENTS.md in the current directory.")
def install_cmd(project: bool) -> None:
    """Install ozm hooks system-wide. Use --project to also write agent docs."""
    click.echo("ozm: installing...")
    _write_hook_script()
    _configure_claude_code()
    if project:
        _write_file("CLAUDE.md", CLAUDE_MD)
        _write_file("AGENTS.md", AGENTS_MD)
    click.echo("ozm: done")

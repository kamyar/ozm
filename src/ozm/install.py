#!/usr/bin/env python3
"""Install ozm hooks and agent configuration."""

import json
import os
import shutil
import stat

import click

OZM_DIR = os.path.expanduser("~/.ozm")
HOOKS_DIR = os.path.join(OZM_DIR, "hooks")
ENFORCE_HOOK = os.path.join(HOOKS_DIR, "enforce.sh")
CODEX_CONFIG = os.path.expanduser("~/.codex/config.toml")
CODEX_RULES_DIR = os.path.expanduser("~/.codex/rules")
CODEX_RULES = os.path.join(CODEX_RULES_DIR, "ozm-enforcement.rules")

HOOK_SCRIPT = r'''#!/usr/bin/env python3
import json, sys, re, shlex, os

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_input = data.get("tool_input", {})
command = tool_input.get("command", "") or tool_input.get("cmd", "")
if not command:
    sys.exit(0)

def deny(reason):
    reason = (
        reason
        + " Include --agent-name \"<what you are working on>\" and "
        "--agent-description \"<one-line intent>\" on ozm commands. "
        "If you forgot this, write it to memory before retrying."
    )
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.exit(0)

SAFE = {"echo", "printf", "pwd", "date", "true", "false", "test"}
SED_ALTERNATIVES = (
    "sed is disallowed because it can edit files in-place and cannot be safely "
    "blanket-approved. Use rg for searching, cat/nl/head/tail for viewing, or "
    "write a small reviewed script and run it with 'ozm run <script>' for "
    "transformations."
)
DISALLOWED = {"sed": SED_ALTERNATIVES, "gsed": SED_ALTERNATIVES}
UNSAFE_PATTERNS = re.compile(r"\$\(|`|<\(|>\(|\$\{")

def split_shell_parts(command):
    parts = []
    start = 0
    quote = None
    escaped = False
    i = 0
    while i < len(command):
        ch = command[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            part = command[start:i].strip()
            if part:
                parts.append(part)
            i += 2
            start = i
            continue
        if ch in (";", "|", "\n"):
            part = command[start:i].strip()
            if part:
                parts.append(part)
            i += 1
            start = i
            continue
        i += 1
    part = command[start:].strip()
    if part:
        parts.append(part)
    return parts

def is_env_assignment(token):
    name, sep, _value = token.partition("=")
    return bool(sep and name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))

def first_word(part):
    try:
        words = shlex.split(part, posix=True)
    except Exception:
        words = part.split()
    if not words:
        return ""
    index = 0
    while index < len(words) and is_env_assignment(words[index]):
        index += 1
    if index < len(words) and os.path.basename(words[index]) == "env":
        index += 1
        while index < len(words):
            token = words[index]
            if token == "--":
                index += 1
                break
            if is_env_assignment(token) or token in {"-i", "--ignore-environment"}:
                index += 1
                continue
            if token in {"-u", "--unset"} and index + 1 < len(words):
                index += 2
                continue
            if token.startswith("-u") or token.startswith("--unset="):
                index += 1
                continue
            break
    if index >= len(words):
        return ""
    return os.path.basename(words[index])

def _flag_value(words, flag):
    for i, word in enumerate(words):
        if word == flag and i + 1 < len(words):
            return words[i + 1]
        if word.startswith(flag + "="):
            return word.split("=", 1)[1]
    return None

def validate_ozm_metadata(part):
    try:
        words = shlex.split(part, posix=True)
    except Exception:
        return "Could not parse ozm command for agent metadata."
    if len(words) < 2 or words[1] not in {"run", "cmd", "git"}:
        return None
    name = _flag_value(words, "--agent-name")
    description = _flag_value(words, "--agent-description")
    if name is None or description is None:
        return "ozm run/cmd/git requires --agent-name and --agent-description."
    if not name.strip():
        return "--agent-name must not be empty."
    if not description.strip():
        return "--agent-description must not be empty."
    if "\n" in name or "\r" in name:
        return "--agent-name must be one line."
    if "\n" in description or "\r" in description:
        return "--agent-description must be exactly one line."
    return None

def has_top_level_redirection(part):
    quote = None
    escaped = False
    for ch in part:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch in (">", "<"):
            return True
    return False

if UNSAFE_PATTERNS.search(command):
    deny("Command contains shell expansion ($(), ``, <(), ${}) — use 'ozm cmd --agent-name \"<work>\" --agent-description \"<intent>\" ...' instead.")

raw_parts = split_shell_parts(command)
is_compound = len(raw_parts) > 1
for raw_part in raw_parts:
    raw_part = raw_part.strip()
    if not raw_part:
        continue
    word = first_word(raw_part)
    if word in DISALLOWED:
        deny(DISALLOWED[word])
    if word == "ozm":
        violation = validate_ozm_metadata(raw_part)
        if violation:
            deny(violation)
        if has_top_level_redirection(raw_part):
            deny("Top-level redirection around ozm commands is not allowed - put redirection inside a reviewed script or run without shell redirection.")
        continue
    if word in SAFE:
        if is_compound or has_top_level_redirection(raw_part):
            deny(f"Use 'ozm cmd --agent-name \"<work>\" --agent-description \"<intent>\" {raw_part.strip()}' instead of running commands directly.")
        continue
    if word == "git":
        deny("Use 'ozm git --agent-name \"<work>\" --agent-description \"<intent>\" <subcommand>' instead of 'git' directly.")
    deny(f"Use 'ozm cmd --agent-name \"<work>\" --agent-description \"<intent>\" {raw_part.strip()}' instead of running commands directly. For script files use 'ozm run --agent-name \"<work>\" --agent-description \"<intent>\" <script>'.")
'''

CODEX_RULES_CONTENT = """# Codex command approval policy for ozm.
# This file is additive. It does not replace default.rules.
# Codex applies the most restrictive matching rule, so these forbidden rules
# override older direct-command allow rules.

prefix_rule(
    pattern = ["ozm"],
    decision = "allow",
    justification = "All shell work must go through ozm.",
    match = [
        "ozm cmd --agent-name inspect-tree --agent-description list-files ls",
        "ozm git --agent-name check-status --agent-description inspect-git-state status",
        "ozm run --agent-name run-tests --agent-description execute-test-script ./scripts/test.sh",
    ],
)

prefix_rule(
    pattern = [[
        "git", "gh", "swift", "npm", "pnpm", "yarn", "bun", "uv",
        "python", "python3", "pip", "pip3", "cargo", "go", "make",
        "cmake", "xcodebuild", "bash", "sh", "zsh", "/bin/bash",
        "/bin/sh", "/bin/zsh", "./scripts/run.sh", "./scripts/test.sh",
    ]],
    decision = "forbidden",
    justification = "Use `ozm cmd/run/git --agent-name ... --agent-description ...` so every approval has agent context.",
    match = [
        "git status",
        "gh pr view 1",
        "swift test",
        "python3 -m pytest",
        "bash -lc ls",
        "./scripts/test.sh",
    ],
    not_match = [
        "ozm git --agent-name check-status --agent-description inspect-git-state status",
        "ozm cmd --agent-name swift-tests --agent-description run-swift-tests swift test",
        "ozm run --agent-name test-script --agent-description execute-test-script ./scripts/test.sh",
    ],
)
"""

CODEX_HOOK_BLOCK = """

[[hooks.PreToolUse]]
matcher = "^(Bash|shell_command|exec_command)$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "{hook}"
timeout = 10
statusMessage = "Checking command with ozm"

[[hooks.PermissionRequest]]
matcher = "^(Bash|shell_command|exec_command)$"

[[hooks.PermissionRequest.hooks]]
type = "command"
command = "{hook}"
timeout = 10
statusMessage = "Checking approval request with ozm"
"""

CLAUDE_MD = """# ozm — script execution gate

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
"""

AGENTS_MD = """# ozm — script execution gate

All script execution and git operations must go through `ozm`.

## Rules

- **Always identify the agent work:** every `ozm run`, `ozm cmd`, and `ozm git` invocation must include `--agent-name "<what you are working on>"` and `--agent-description "<one-line intent>"`.
- **Run scripts:** `ozm run --agent-name "<work>" --agent-description "<intent>" <script> [args...]` — never `python`, `bash`, `./`, or `uv run` directly
- **Run commands:** `ozm cmd --agent-name "<work>" --agent-description "<intent>" <command> [args...]` — for arbitrary commands (e.g. `ozm cmd --agent-name "Install deps" --agent-description "Install editable package dependencies." uv pip install -e .`)
- **Avoid sed:** `sed`/`gsed` are blocked because they can edit files in-place. Use `rg` for searching, `cat`/`nl`/`head`/`tail` for viewing, or `ozm run <script>` for transformations.
- **Commit:** `ozm git --agent-name "<work>" --agent-description "<intent>" commit -m "short message"` — max 72 char subject, max 500 chars total
- **Push:** `ozm git --agent-name "<work>" --agent-description "<intent>" push` — no force push, no pushing to main/master

## Scripts must have a shebang

Always include a shebang line (e.g. `#!/usr/bin/env python3`, `#!/usr/bin/env bash`) as the first line of any script you create. This allows `ozm run` to execute it directly. Never use `ozm cmd python script.py` or `ozm cmd uv run python script.py` — use `ozm run --agent-name "<work>" --agent-description "<intent>" script.py` instead.

Keep commit messages short. No heredoc/EOF patterns. Simple `-m "message"` only.

## Override blocked operations

If a command is blocked but you believe it's necessary, use `--reason` to request a one-time override from the user:

    ozm git --agent-name "Ship hotfix" --agent-description "Push the production fix branch." push --reason "Hotfix for production outage, needs to go to main"
    ozm cmd --agent-name "Clean build" --agent-description "Remove generated build artifacts before rebuild." rm -rf build/ --reason "Clean build artifacts before rebuild"

The user sees your reasoning in a dialog and can approve once. This is never cached or added to allowlists.

If ozm rejects a command because agent metadata is missing or invalid, write the metadata requirement to memory before retrying.
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


def _backup(path: str) -> None:
    if not os.path.exists(path):
        return
    backup = f"{path}.bak-ozm"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)


def _ensure_codex_hooks_feature(config: str) -> str:
    lines = config.splitlines()
    out = []
    in_features = False
    saw_features = False
    saw_codex_hooks = False

    for line in lines:
        stripped = line.strip()
        is_section = stripped.startswith("[") and stripped.endswith("]")
        if stripped == "[features]":
            saw_features = True
            in_features = True
            saw_codex_hooks = False
            out.append(line)
            continue
        if in_features and is_section:
            if not saw_codex_hooks:
                out.append("codex_hooks = true")
            in_features = False
        if in_features and stripped.startswith("codex_hooks"):
            out.append("codex_hooks = true")
            saw_codex_hooks = True
            continue
        out.append(line)

    if in_features and not saw_codex_hooks:
        out.append("codex_hooks = true")

    result = "\n".join(out).rstrip()
    if not saw_features:
        result += "\n\n[features]\ncodex_hooks = true"
    return result + "\n"


def _configure_codex() -> None:
    os.makedirs(CODEX_RULES_DIR, exist_ok=True)
    if os.path.exists(CODEX_RULES):
        with open(CODEX_RULES) as f:
            existing_rules = f.read()
    else:
        existing_rules = None
    if existing_rules != CODEX_RULES_CONTENT:
        _backup(CODEX_RULES)
        with open(CODEX_RULES, "w") as f:
            f.write(CODEX_RULES_CONTENT)
        click.echo(f"  codex rules: {CODEX_RULES}")
    else:
        click.echo(f"  codex rules: {CODEX_RULES}")

    os.makedirs(os.path.dirname(CODEX_CONFIG), exist_ok=True)
    if os.path.exists(CODEX_CONFIG):
        with open(CODEX_CONFIG) as f:
            config = f.read()
    else:
        config = ""

    next_config = _ensure_codex_hooks_feature(config)
    hook_block = CODEX_HOOK_BLOCK.format(hook=ENFORCE_HOOK)
    if ENFORCE_HOOK not in next_config:
        next_config = next_config.rstrip() + hook_block + "\n"

    if next_config != config:
        _backup(CODEX_CONFIG)
        with open(CODEX_CONFIG, "w") as f:
            f.write(next_config)
    click.echo(f"  codex: {CODEX_CONFIG}")


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
    _configure_codex()
    if project:
        from ozm.config import find_project_root

        root = find_project_root()
        _write_file(os.path.join(root, "CLAUDE.md"), CLAUDE_MD)
        _write_file(os.path.join(root, "AGENTS.md"), AGENTS_MD)
    click.echo("ozm: done")

# Oberzugriffsmeister (ozm)

Let AI agents run free — without giving up control.

AI coding agents are powerful, but they need to execute commands: installing packages, running tests, writing and executing scripts. Most setups force a choice — either babysit every command, or trust the agent blindly.

`ozm` gives you a third option. It sits between the agent and your shell, gating every command through a content-aware approval system. Approve once, run forever — until something changes. Per-project allowlists let you pre-approve safe commands (`pytest`, `uv run`, etc.) so the agent flows uninterrupted, while unfamiliar commands still require your sign-off. When you deny a command, you can type feedback directly in the dialog — the agent sees it and adjusts.

No more clicking through identical permission prompts. No more worrying about what the agent just ran.

## Install

```
uv tool install ozm
```

## Quick start

```bash
cd your-project
ozm install    # hooks into Claude Code, writes CLAUDE.md + AGENTS.md
```

That's it. From now on, the agent routes all commands through `ozm`.

## Commands

```
$ ozm --help
Usage: ozm [OPTIONS] COMMAND [ARGS]...

  Content-aware script execution gate and git rule enforcer.

Commands:
  run      Run a script after content review (hash-gated).
  cmd      Run an arbitrary command after approval.
  git      Git pass-through. Enforces rules on commit and push.
  install  Install ozm hooks and agent configuration in the current project.
  status   Show tracked files and commands with their approval status.
  reset    Forget approval for a script (or all scripts with --all).
```

### `ozm run` — script execution gate

```
$ ozm run script.py [args...]
```

First time (or after the script changes):
- Opens the file in your editor (`$VISUAL` / `$EDITOR`) or Quick Look
- Shows a native macOS dialog — Allow, or Deny with feedback
- On Allow: records the SHA-256 content hash and executes
- On Deny: exits without running; feedback is printed to stderr for the agent

Subsequent runs (unchanged file): executes immediately, no prompt.

### `ozm cmd` — arbitrary command approval

```
$ ozm cmd uv pip install -e .
$ ozm cmd ls -la
```

Same approval flow, but for commands instead of files. The command string is hashed — approve it once and it runs without prompting until you reset it.

### `ozm git` — safe git pass-through

```
$ ozm git commit -m "message"    # enforces 72-char subject, 500-char total
$ ozm git push                   # blocks --force and pushes to main/master
```

### `ozm status` / `ozm reset`

```
$ ozm status
  [     ok] /path/to/script.py
  [CHANGED] /path/to/other.py
  [     ok] cmd:uv pip install -e .

$ ozm reset script.py     # forget one approval
$ ozm reset --all          # forget all approvals for this project
```

## Per-project allowlists

Create a `.ozm.yaml` in your project root to pre-approve safe commands:

```yaml
allowed_commands:
  - pytest
  - "uv run *"
  - "uv pip install *"
  - "git push origin main:main"
```

Commands matching these patterns skip the approval dialog entirely. Patterns use glob syntax and match against both the full command and the first word.

Approvals are project-scoped — approving `pytest` in one project doesn't carry over to another.

## How it works

1. `ozm install` registers a Claude Code `PreToolUse` hook that intercepts all Bash commands
2. The hook blocks direct execution and forces everything through `ozm run`, `ozm cmd`, or `ozm git`
3. Each command/script goes through: `.ozm.yaml` allowlist -> project-scoped hash cache (`~/.ozm/hashes.yaml`) -> approval dialog
4. Approved content hashes are stored per-project, so unchanged commands/scripts run instantly

On macOS, approval dialogs use native `osascript` with an inline text field for feedback. Supported editors for auto-close: VS Code, Cursor, VSCodium, Code Insiders.

## Configuration

| Env var   | Purpose                          | Example        |
|-----------|----------------------------------|----------------|
| `VISUAL`  | Editor for reviewing scripts     | `code`         |
| `EDITOR`  | Fallback if `VISUAL` is not set  | `vim`          |

If neither is set, macOS Quick Look is used.

## Requirements

- Python 3.12+
- macOS (for native approval dialogs; falls back to stdout review on other platforms)

# Oberzugriffsmeister (ozm)

Content-aware script execution gate and git rule enforcer for AI-assisted development.

When AI agents write scripts and need to run them, `ozm` ensures you review the content before execution. Once approved, the same script runs without interruption — until it changes.

## Install

```
uv tool install oberzugriffsmeister
```

## Setup

```bash
ozm install
```

Configures the current project: writes `CLAUDE.md`, `AGENTS.md`, and installs a Claude Code hook that forces all commands through `ozm`.

## Usage

### Script execution gate

```bash
ozm run script.py [args...]
```

**First run (new or changed file):**
- Opens the file in your editor (`$VISUAL` / `$EDITOR`) or Quick Look
- Shows a native macOS approval dialog (Allow / Deny)
- On Allow: records the content hash and executes
- On Deny: exits without running or recording

**Subsequent runs (unchanged file):**
- Executes immediately, no prompt

**Fallback (no GUI available):**
- Prints the file content to stdout and exits
- Run the same command again to execute

### Git wrappers

```bash
ozm git commit -m "message"
```

Enforces:
- Subject line max 72 characters
- Total message max 500 characters

```bash
ozm git push
```

Blocks:
- Force pushes (`--force`, `-f`)
- Pushes to `main` or `master`

### Arbitrary commands

```bash
ozm cmd uv pip install -e .
ozm cmd ls -la
```

Pass-through for any command that isn't a script file.

### Management

```bash
ozm status          # show tracked files and approval state
ozm reset script.py # forget approval for a file
ozm reset --all     # forget all approvals
```

## How it works

`ozm run` maintains a `~/.ozm/hashes.yaml` file mapping script paths to their SHA-256 content hashes.

On macOS, the approval dialog and editor integration use `osascript` and System Events. Supported editors for auto-close: VS Code, Cursor, VSCodium, Code Insiders.

## Configuration

| Env var   | Purpose                          | Example        |
|-----------|----------------------------------|----------------|
| `VISUAL`  | Editor for reviewing scripts     | `code`         |
| `EDITOR`  | Fallback if `VISUAL` is not set  | `vim`          |

If neither is set, macOS Quick Look is used.

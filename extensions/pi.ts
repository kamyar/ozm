import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createBashTool, getAgentDir } from "@earendil-works/pi-coding-agent";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { join, resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const AGENT_NAME = "pi";
const AGENT_DESCRIPTION = "Run a shell command requested by pi through ozm.";
const SCRIPT_DESCRIPTION = "Run a reviewed shell script generated from a pi bash command.";
const OZM_SCRIPT_DIR = join(getAgentDir(), "ozm", "scripts");
const SETTINGS_PATH = join(getAgentDir(), "settings.json");
const SAFE_DIRECT_COMMANDS = new Set(["echo", "printf", "pwd", "date", "true", "false", "test"]);

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function ozmMetadata(description = AGENT_DESCRIPTION): string {
  return `--agent-name ${shellQuote(AGENT_NAME)} --agent-description ${shellQuote(description)}`;
}

function readShellCommandPrefix(): string | undefined {
  try {
    const settings = JSON.parse(readFileSync(SETTINGS_PATH, "utf8")) as { shellCommandPrefix?: unknown };
    return typeof settings.shellCommandPrefix === "string" ? settings.shellCommandPrefix : undefined;
  } catch {
    return undefined;
  }
}

function hasShellMetacharacters(command: string): boolean {
  const metacharacters = new Set([";", "|", "&", "$", "`", "\n", "(", ")", "<", ">", "{", "}", "[", "]"]);
  let quote: string | undefined;
  let escaped = false;

  for (const ch of command) {
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (ch === quote) {
        quote = undefined;
      } else if (quote === '"' && (ch === "$" || ch === "`")) {
        return true;
      }
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    if (metacharacters.has(ch)) {
      return true;
    }
  }

  return quote !== undefined;
}

function shellWords(command: string): string[] | undefined {
  const words: string[] = [];
  let current = "";
  let quote: string | undefined;
  let escaped = false;
  let sawToken = false;

  for (const ch of command) {
    if (escaped) {
      current += ch;
      escaped = false;
      sawToken = true;
      continue;
    }
    if (ch === "\\" && quote !== "'") {
      escaped = true;
      sawToken = true;
      continue;
    }
    if (quote) {
      if (ch === quote) {
        quote = undefined;
      } else {
        current += ch;
      }
      sawToken = true;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      sawToken = true;
      continue;
    }
    if (/\s/.test(ch)) {
      if (sawToken) {
        words.push(current);
        current = "";
        sawToken = false;
      }
      continue;
    }
    current += ch;
    sawToken = true;
  }

  if (escaped || quote) {
    return undefined;
  }
  if (sawToken) {
    words.push(current);
  }
  return words;
}

function isEnvAssignment(token: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*=.*/.test(token);
}

function commandStartIndex(words: string[]): number | undefined {
  let index = 0;
  while (index < words.length && isEnvAssignment(words[index])) {
    index += 1;
  }
  if (index < words.length && words[index].split("/").pop() === "env") {
    index += 1;
    while (index < words.length) {
      const token = words[index];
      if (token === "--") {
        index += 1;
        break;
      }
      if (isEnvAssignment(token) || token === "-i" || token === "--ignore-environment") {
        index += 1;
        continue;
      }
      if ((token === "-u" || token === "--unset") && index + 1 < words.length) {
        index += 2;
        continue;
      }
      if (token.startsWith("-u") || token.startsWith("--unset=")) {
        index += 1;
        continue;
      }
      break;
    }
  }
  return index < words.length ? index : undefined;
}

function commandName(words: string[]): string | undefined {
  const index = commandStartIndex(words);
  if (index === undefined) {
    return undefined;
  }
  return words[index].split("/").pop();
}

function hasLeadingShellEnvAssignment(words: string[]): boolean {
  return words.length > 0 && isEnvAssignment(words[0]);
}

function quotedWords(words: string[]): string {
  return words.map(shellQuote).join(" ");
}

function isLikelyScript(words: string[], cwd: string): boolean {
  const index = commandStartIndex(words);
  if (index === undefined) {
    return false;
  }
  const candidate = words[index];
  if (!candidate.startsWith("./") && !candidate.startsWith("../") && !candidate.startsWith("/")) {
    return false;
  }
  return existsSync(resolve(cwd, candidate));
}

function writeReviewedScript(command: string, cwd: string): string {
  mkdirSync(OZM_SCRIPT_DIR, { recursive: true });
  const hash = createHash("sha256").update(cwd).update("\0").update(command).digest("hex").slice(0, 16);
  const path = join(OZM_SCRIPT_DIR, `pi-bash-${hash}.sh`);
  const content = `#!/usr/bin/env bash\n# Generated by pi's ozm integration. Review this command before approving ozm.\n${command}\n`;
  if (!existsSync(path) || readFileSync(path, "utf8") !== content) {
    writeFileSync(path, content, "utf8");
    chmodSync(path, 0o755);
  }
  return path;
}

function previewThenRunScript(scriptPath: string, ozmRunArgs: string, description = SCRIPT_DESCRIPTION): string {
  const previewStart = `----- BEGIN ozm script review: ${scriptPath} -----`;
  const previewEnd = "----- END ozm script review -----";
  return [
    `printf '%s\\n' ${shellQuote(previewStart)}`,
    `/bin/cat -- ${shellQuote(scriptPath)}`,
    `printf '%s\\n' ${shellQuote(previewEnd)}`,
    `ozm run ${ozmMetadata(description)} -- ${ozmRunArgs}`,
  ].join("; ");
}

function routeThroughOzm(command: string, cwd: string): string {
  const trimmed = command.trim();
  if (!trimmed) {
    return command;
  }

  const words = shellWords(trimmed);
  const name = words ? commandName(words) : undefined;
  const startIndex = words ? commandStartIndex(words) : undefined;
  const hasMeta = hasShellMetacharacters(trimmed) || !words;
  const needsShell = Boolean(words && hasLeadingShellEnvAssignment(words));

  if (!hasMeta && !needsShell && name === "ozm") {
    return trimmed;
  }

  if (!hasMeta && !needsShell && startIndex === 0 && name && SAFE_DIRECT_COMMANDS.has(name)) {
    return trimmed;
  }

  if (!hasMeta && !needsShell && name === "git" && startIndex !== undefined) {
    const rest = quotedWords(words!.slice(startIndex + 1));
    return `ozm git ${ozmMetadata()} -- ${rest}`.trim();
  }

  if (!hasMeta && !needsShell && words && isLikelyScript(words, cwd) && startIndex !== undefined) {
    const scriptPath = resolve(cwd, words[startIndex]);
    return previewThenRunScript(scriptPath, quotedWords(words.slice(startIndex)));
  }

  if (!hasMeta && !needsShell && words) {
    return `ozm cmd ${ozmMetadata()} -- ${quotedWords(words)}`;
  }

  const scriptPath = writeReviewedScript(trimmed, cwd);
  return previewThenRunScript(scriptPath, shellQuote(scriptPath));
}

async function runOzm(args: string[], ctx: ExtensionContext): Promise<{ ok: boolean; output: string }> {
  try {
    const { stdout, stderr } = await execFileAsync("ozm", args, {
      cwd: ctx.cwd,
      maxBuffer: 1024 * 1024,
      env: process.env,
    });
    return { ok: true, output: `${stdout}${stderr}`.trim() };
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string; message?: string };
    const output = `${err.stdout ?? ""}${err.stderr ?? ""}`.trim() || err.message || String(error);
    return { ok: false, output };
  }
}

function hasTrustedProjectConfig(configOutput: string): boolean | undefined {
  const status = configOutput.match(/^status:\s+(.*)$/m)?.[1]?.trim();
  if (!status) {
    return undefined;
  }
  return status === "exists";
}

async function bootstrapCheck(ctx: ExtensionContext, notifyWhenHealthy = false) {
  const version = await runOzm(["version"], ctx);
  if (!version.ok) {
    ctx.ui.notify("ozm is not available. Install with: brew tap kamyar/ozm https://github.com/kamyar/ozm && brew install ozm", "warning");
    return;
  }

  const config = await runOzm(["config"], ctx);
  const trusted = config.ok ? hasTrustedProjectConfig(config.output) : undefined;
  const hasRepoConfig = existsSync(resolve(ctx.cwd, ".ozm.yaml"));
  if (hasRepoConfig && trusted === false) {
    ctx.ui.notify("ozm: .ozm.yaml exists but is not trusted yet. Run /ozm-trust to snapshot it into ~/.ozm/projects/.", "warning");
    return;
  }

  if (notifyWhenHealthy) {
    ctx.ui.notify(`ozm ready (${version.output.split("\n")[0]})`, "info");
  }
}

export default function (pi: ExtensionAPI) {
  const cwd = process.cwd();
  const bashTool = createBashTool(cwd, {
    commandPrefix: readShellCommandPrefix(),
    spawnHook: ({ command, cwd, env }) => ({
      command: routeThroughOzm(command, cwd),
      cwd,
      env: { ...env, PI_OZM_INTEGRATION: "1" },
    }),
  });

  pi.registerTool({
    ...bashTool,
    label: "bash (ozm)",
    execute: async (id, params, signal, onUpdate) => bashTool.execute(id, params, signal, onUpdate),
  });

  pi.on("session_start", async (_event, ctx) => {
    ctx.ui.setStatus("ozm", "ozm active");
    await bootstrapCheck(ctx);
  });

  pi.on("before_agent_start", async (event) => ({
    systemPrompt: `${event.systemPrompt}\n\nOzm integration is active for the bash tool. Simple commands are routed through ozm cmd/git/run with pi-provided agent metadata. Shell-compound commands are written to reviewed scripts and executed through ozm run. If ozm asks for changes, follow the ozm feedback exactly.`,
  }));

  pi.registerCommand("ozm-bootstrap", {
    description: "Check ozm availability and trust current-repo ozm policy when present",
    handler: async (_args, ctx) => {
      await bootstrapCheck(ctx, true);
      const hasRepoConfig = existsSync(resolve(ctx.cwd, ".ozm.yaml"));
      if (!hasRepoConfig) {
        return;
      }
      const config = await runOzm(["config"], ctx);
      if (config.ok && hasTrustedProjectConfig(config.output) === false) {
        const ok = await ctx.ui.confirm("Trust ozm project config", "Run `ozm trust` to copy this repo's .ozm.yaml into ~/.ozm/projects/? This is a user-owned trust action.");
        if (ok) {
          const trust = await runOzm(["trust"], ctx);
          ctx.ui.notify(trust.output || (trust.ok ? "ozm trust completed" : "ozm trust failed"), trust.ok ? "info" : "error");
        }
      }
    },
  });

  pi.registerCommand("ozm-trust", {
    description: "Run ozm trust for the current project",
    handler: async (_args, ctx) => {
      const result = await runOzm(["trust"], ctx);
      ctx.ui.notify(result.output || (result.ok ? "ozm trust completed" : "ozm trust failed"), result.ok ? "info" : "error");
    },
  });

  pi.registerCommand("ozm-status", {
    description: "Show ozm approval status for the current project",
    handler: async (_args, ctx) => {
      const result = await runOzm(["status"], ctx);
      ctx.ui.notify(result.output || "ozm status returned no output", result.ok ? "info" : "error");
    },
  });

  pi.registerCommand("ozm-config", {
    description: "Show ozm config paths for the current project",
    handler: async (_args, ctx) => {
      const result = await runOzm(["config"], ctx);
      ctx.ui.notify(result.output || "ozm config returned no output", result.ok ? "info" : "error");
    },
  });

  pi.registerCommand("ozm-doctor", {
    description: "Run ozm doctor",
    handler: async (_args, ctx) => {
      const result = await runOzm(["doctor"], ctx);
      ctx.ui.notify(result.output || "ozm doctor returned no output", result.ok ? "info" : "error");
    },
  });

  pi.registerCommand("ozm-log", {
    description: "Show recent ozm audit log entries",
    handler: async (args, ctx) => {
      const parsed = args.trim() ? args.trim().split(/\s+/) : [];
      const result = await runOzm(["log", ...parsed], ctx);
      ctx.ui.notify(result.output || "ozm log returned no output", result.ok ? "info" : "error");
    },
  });
}

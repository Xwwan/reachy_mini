import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const repoRoot = path.resolve(process.cwd(), "..");
const appsRoot = path.join(repoRoot, "apps");
const descriptorName = "reachy-app.json";
const port = Number(process.env.APP_MANAGER_PORT ?? 8787);
const host = process.env.APP_MANAGER_HOST ?? "127.0.0.1";
const terminalStates = new Set(["done", "error"]);

let current = null;

function displayName(slug) {
  return slug
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function firstMarkdownParagraph(markdown) {
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith("---")) {
      continue;
    }
    return line;
  }
  return "";
}

async function readReadmeInfo(appDir) {
  const readmePath = path.join(appDir, "README.md");
  if (!existsSync(readmePath)) {
    return { title: null, description: "", hasReadme: false };
  }

  const content = await readFile(readmePath, "utf8");
  const title = content.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? null;
  return {
    title,
    description: firstMarkdownParagraph(content),
    hasReadme: true,
  };
}

async function loadDescriptor(appDir, fallbackId) {
  const descriptorPath = path.join(appDir, descriptorName);
  if (!existsSync(descriptorPath)) {
    return null;
  }

  const descriptor = JSON.parse(await readFile(descriptorPath, "utf8"));
  return {
    id: descriptor.id ?? fallbackId,
    title: descriptor.title ?? displayName(fallbackId),
    description: descriptor.description ?? "",
    command: Array.isArray(descriptor.command) ? descriptor.command : [],
    setupCommand: Array.isArray(descriptor.setupCommand) ? descriptor.setupCommand : [],
    setupHint: descriptor.setupHint ?? "",
    frontendUrl: descriptor.frontendUrl ?? null,
    healthUrl: descriptor.healthUrl ?? null,
    stopSignal: descriptor.stopSignal ?? "SIGINT",
    env: descriptor.env ?? {},
    cwd: descriptor.cwd ?? null,
  };
}

async function scanAppsWithDescriptors() {
  if (!existsSync(appsRoot)) {
    return [];
  }

  const entries = await readdir(appsRoot, { withFileTypes: true });
  const apps = [];

  for (const entry of entries) {
    if (entry.name.startsWith(".")) {
      continue;
    }

    const appDir = path.join(appsRoot, entry.name);
    const appStat = await stat(appDir).catch(() => null);
    if (!appStat?.isDirectory()) {
      continue;
    }

    const descriptor = await loadDescriptor(appDir, entry.name);
    const readme = await readReadmeInfo(appDir).catch(() => ({
      title: null,
      description: "",
      hasReadme: existsSync(path.join(appDir, "README.md")),
    }));

    const id = descriptor?.id ?? entry.name;
    const info = {
      id,
      title: descriptor?.title ?? readme.title ?? displayName(entry.name),
      description: descriptor?.description ?? readme.description,
      path: path.relative(repoRoot, appDir),
      hasReadme: readme.hasReadme,
      hasDescriptor: Boolean(descriptor),
      startable: Boolean(descriptor?.command?.length),
      command: descriptor?.command ?? [],
      setupCommand: descriptor?.setupCommand ?? [],
      setupHint: descriptor?.setupHint ?? "",
      frontendUrl: descriptor?.frontendUrl ?? null,
      healthUrl: descriptor?.healthUrl ?? null,
    };
    apps.push({ info, descriptor, appDir });
  }

  apps.sort((a, b) => a.info.title.localeCompare(b.info.title));
  return apps;
}

async function listApps() {
  return (await scanAppsWithDescriptors()).map(({ info }) => info);
}

async function findApp(appId) {
  const apps = await scanAppsWithDescriptors();
  const match = apps.find(({ info }) => info.id === appId);
  if (!match) {
    throw httpError(404, `App '${appId}' was not found.`);
  }
  if (!match.descriptor?.command?.length) {
    throw httpError(400, `App '${appId}' has no start command. Add ${descriptorName}.`);
  }
  return match;
}

function currentStatus() {
  if (!current) {
    return null;
  }

  return {
    app: current.app,
    state: current.state,
    pid: current.process.exitCode === null ? current.process.pid : null,
    startedAt: current.startedAt,
    returnCode: current.returnCode,
    error: current.error,
  };
}

async function startApp(appId) {
  if (current && !terminalStates.has(current.state)) {
    if (current.app.id === appId) {
      return currentStatus();
    }
    await stopCurrentApp();
  }

  const { info, descriptor, appDir } = await findApp(appId);
  const cwd = descriptor.cwd ? path.resolve(appDir, descriptor.cwd) : appDir;
  const child = spawn(descriptor.command[0], descriptor.command.slice(1), {
    cwd,
    env: { ...process.env, ...descriptor.env },
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });

  const running = {
    app: info,
    descriptor,
    process: child,
    state: "running",
    startedAt: Date.now() / 1000,
    returnCode: null,
    error: null,
    stderrLines: [],
  };
  current = running;

  child.stdout?.on("data", (chunk) => {
    process.stdout.write(`[${appId}] ${chunk}`);
  });

  child.stderr?.on("data", (chunk) => {
    const text = chunk.toString();
    for (const line of text.split(/\r?\n/).filter(Boolean)) {
      running.stderrLines.push(line);
    }
    process.stderr.write(`[${appId}:stderr] ${text}`);
  });

  child.on("error", (error) => {
    running.state = "error";
    running.error = error.message;
  });

  child.on("exit", (code) => {
    running.returnCode = code;
    if (current?.process === child) {
      running.state = code === 0 ? "done" : "error";
      running.error =
        code === 0 ? null : running.stderrLines.slice(-10).join("\n") || `Exited with code ${code}`;
    }
  });

  return currentStatus();
}

async function stopCurrentApp() {
  if (!current) {
    return { ok: true };
  }

  const running = current;
  running.state = "stopping";

  if (running.process.exitCode === null) {
    const signal = normalizeSignal(running.descriptor.stopSignal);
    try {
      if (process.platform !== "win32") {
        process.kill(-running.process.pid, signal);
      } else {
        running.process.kill(signal);
      }
    } catch (error) {
      if (error.code !== "ESRCH") {
        throw error;
      }
    }

    await waitForExit(running.process, 10_000).catch(() => {
      if (running.process.exitCode === null) {
        try {
          if (process.platform !== "win32") {
            process.kill(-running.process.pid, "SIGKILL");
          } else {
            running.process.kill("SIGKILL");
          }
        } catch {
          // The process already exited.
        }
      }
    });
  }

  if (current?.process === running.process) {
    current = null;
  }
  return { ok: true };
}

async function restartApp(appId) {
  await stopCurrentApp();
  return startApp(appId);
}

function normalizeSignal(signal) {
  const normalized = String(signal || "SIGINT").toUpperCase();
  return normalized.startsWith("SIG") ? normalized : `SIG${normalized}`;
}

function waitForExit(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    if (child.exitCode !== null) {
      resolve();
      return;
    }

    const timer = setTimeout(() => reject(new Error("Timed out while stopping app.")), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

function httpError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

async function route(method, pathname) {
  if (method === "GET" && pathname === "/api/local-apps") {
    return listApps();
  }

  if (method === "GET" && pathname === "/api/local-apps/current") {
    return currentStatus();
  }

  if (method === "POST" && pathname === "/api/local-apps/current/stop") {
    return stopCurrentApp();
  }

  const startMatch = pathname.match(/^\/api\/local-apps\/([^/]+)\/start$/);
  if (method === "POST" && startMatch) {
    return startApp(decodeURIComponent(startMatch[1]));
  }

  const restartMatch = pathname.match(/^\/api\/local-apps\/([^/]+)\/restart$/);
  if (method === "POST" && restartMatch) {
    return restartApp(decodeURIComponent(restartMatch[1]));
  }

  throw httpError(404, "Not found.");
}

const server = createServer(async (request, response) => {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (request.method === "OPTIONS") {
    response.writeHead(204);
    response.end();
    return;
  }

  try {
    const url = new URL(request.url ?? "/", `http://${request.headers.host}`);
    const payload = await route(request.method ?? "GET", url.pathname);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(payload));
  } catch (error) {
    const status = error.status ?? 500;
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ detail: error.message ?? "Internal server error" }));
  }
});

server.listen(port, host, () => {
  console.log(`Local App Manager listening on http://${host}:${port}`);
});

process.on("SIGINT", async () => {
  await stopCurrentApp().catch(() => {});
  process.exit(0);
});

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, readdir, stat } from "node:fs/promises";
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
const setupJobs = new Map();

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
    python: descriptor.python ?? "python3",
    venv: descriptor.venv ?? null,
    module: descriptor.module ?? null,
    args: Array.isArray(descriptor.args) ? descriptor.args : [],
    command: Array.isArray(descriptor.command) ? descriptor.command : [],
    setup: descriptor.setup ?? null,
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
    const command = descriptor ? resolveCommand(appDir, descriptor) : [];
    const installed = descriptor ? isAppInstalled(appDir, descriptor) : false;
    const setup = descriptor ? setupInfo(id, appDir, descriptor, installed) : emptySetupInfo();
    const configured = command.length > 0;

    const info = {
      id,
      title: descriptor?.title ?? readme.title ?? displayName(entry.name),
      description: descriptor?.description ?? readme.description,
      path: path.relative(repoRoot, appDir),
      hasReadme: readme.hasReadme,
      hasDescriptor: Boolean(descriptor),
      configured,
      startable: configured && installed,
      command,
      python: descriptor?.python ?? null,
      venv: descriptor?.venv ?? null,
      module: descriptor?.module ?? null,
      args: descriptor?.args ?? [],
      installed,
      setup,
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

function resolveCommand(appDir, descriptor) {
  if (descriptor.command.length) {
    return descriptor.command;
  }
  if (!descriptor.module) {
    return [];
  }
  const python = descriptor.venv ? venvPythonPath(appDir, descriptor.venv) : descriptor.python;
  return [python, "-m", descriptor.module, ...descriptor.args];
}

function venvPythonPath(appDir, venv) {
  const relative = process.platform === "win32"
    ? path.join(venv, "Scripts", "python.exe")
    : path.join(venv, "bin", "python");
  return path.join(appDir, relative);
}

function isAppInstalled(appDir, descriptor) {
  if (!descriptor.venv) {
    return true;
  }
  return existsSync(venvPythonPath(appDir, descriptor.venv));
}

function setupInfo(appId, appDir, descriptor, installed) {
  const job = setupJobs.get(appId);
  const available = Boolean(
    descriptor.venv || descriptor.setup?.install?.length || descriptor.setupCommand.length,
  );
  return {
    available,
    required: Boolean(descriptor.venv),
    installed,
    state: job?.state ?? (descriptor.venv && !installed ? "needed" : "ready"),
    error: job?.error ?? null,
    logs: job?.logs?.slice(-30) ?? [],
    venvPath: descriptor.venv ? path.relative(repoRoot, path.join(appDir, descriptor.venv)) : null,
  };
}

function emptySetupInfo() {
  return {
    available: false,
    required: false,
    installed: false,
    state: "unavailable",
    error: null,
    logs: [],
    venvPath: null,
  };
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
  if (!match.info.configured) {
    throw httpError(400, `App '${appId}' has no start command. Add module or command to ${descriptorName}.`);
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
  if (!info.installed) {
    throw httpError(400, `App '${appId}' is not set up yet. Run setup first.`);
  }

  const cwd = descriptor.cwd ? path.resolve(appDir, descriptor.cwd) : appDir;
  const command = resolveCommand(appDir, descriptor);
  const child = spawn(command[0], command.slice(1), {
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

async function setupApp(appId) {
  const { descriptor, appDir, info } = await findApp(appId);
  if (!info.setup.available) {
    throw httpError(400, `App '${appId}' has no setup instructions.`);
  }

  const existing = setupJobs.get(appId);
  if (existing?.state === "running") {
    return setupStatus(appId);
  }

  const job = {
    appId,
    state: "running",
    startedAt: Date.now() / 1000,
    finishedAt: null,
    error: null,
    logs: [],
  };
  setupJobs.set(appId, job);

  runSetupJob(job, appDir, descriptor).catch((error) => {
    job.state = "error";
    job.error = error.message ?? String(error);
    job.finishedAt = Date.now() / 1000;
    appendLog(job, `ERROR: ${job.error}`);
  });

  return job;
}

function setupStatus(appId) {
  return setupJobs.get(appId) ?? {
    appId,
    state: "idle",
    startedAt: null,
    finishedAt: null,
    error: null,
    logs: [],
  };
}

async function runSetupJob(job, appDir, descriptor) {
  const cwd = descriptor.cwd ? path.resolve(appDir, descriptor.cwd) : appDir;
  await mkdir(cwd, { recursive: true });

  if (descriptor.setupCommand.length) {
    await runStep(job, descriptor.setupCommand, cwd, descriptor.env);
  } else {
    if (descriptor.venv) {
      await runStep(job, [descriptor.python, "-m", "venv", descriptor.venv], cwd, descriptor.env);
      const venvPython = venvPythonPath(appDir, descriptor.venv);
      await runStep(job, [venvPython, "-m", "pip", "install", "-U", "pip"], cwd, descriptor.env);
      const installArgs = Array.isArray(descriptor.setup?.install) ? descriptor.setup.install : [];
      if (installArgs.length) {
        await runStep(job, [venvPython, "-m", "pip", "install", ...installArgs], cwd, descriptor.env);
      }
    } else if (Array.isArray(descriptor.setup?.install) && descriptor.setup.install.length) {
      await runStep(
        job,
        [descriptor.python, "-m", "pip", "install", ...descriptor.setup.install],
        cwd,
        descriptor.env,
      );
    }
  }

  job.state = "done";
  job.finishedAt = Date.now() / 1000;
  appendLog(job, "Setup finished successfully.");
}

function runStep(job, command, cwd, env) {
  appendLog(job, `$ ${command.join(" ")}`);
  return new Promise((resolve, reject) => {
    const child = spawn(command[0], command.slice(1), {
      cwd,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });

    child.stdout?.on("data", (chunk) => {
      for (const line of chunk.toString().split(/\r?\n/).filter(Boolean)) {
        appendLog(job, line);
      }
    });
    child.stderr?.on("data", (chunk) => {
      for (const line of chunk.toString().split(/\r?\n/).filter(Boolean)) {
        appendLog(job, line);
      }
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`Command failed with code ${code}: ${command.join(" ")}`));
      }
    });
  });
}

function appendLog(job, line) {
  job.logs.push(line);
  if (job.logs.length > 400) {
    job.logs.splice(0, job.logs.length - 400);
  }
  process.stdout.write(`[setup:${job.appId}] ${line}\n`);
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

  const setupMatch = pathname.match(/^\/api\/local-apps\/([^/]+)\/setup$/);
  if (method === "POST" && setupMatch) {
    return setupApp(decodeURIComponent(setupMatch[1]));
  }

  const setupStatusMatch = pathname.match(/^\/api\/local-apps\/([^/]+)\/setup-status$/);
  if (method === "GET" && setupStatusMatch) {
    return setupStatus(decodeURIComponent(setupStatusMatch[1]));
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

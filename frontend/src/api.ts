import type { LocalAppInfo, LocalAppStatus, SetupStatus } from "./types";

const terminalStates = new Set(["done", "error"]);
const setupTerminalStates = new Set(["done", "error"]);

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function normalizeManagerUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) {
    return "http://127.0.0.1:8787";
  }

  try {
    return new URL(trimmed).toString().replace(/\/+$/, "");
  } catch {
    try {
      return new URL(`http://${trimmed}`).toString().replace(/\/+$/, "");
    } catch {
      return "http://127.0.0.1:8787";
    }
  }
}

export function apiBase(managerUrl: string, useDevProxy: boolean): string {
  return useDevProxy ? "" : normalizeManagerUrl(managerUrl);
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body.message ?? JSON.stringify(body);
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(detail || `Request failed with ${response.status}`, response.status);
  }

  return response.json() as Promise<T>;
}

export async function fetchLocalApps(base: string): Promise<LocalAppInfo[]> {
  const response = await fetch(`${base}/api/local-apps`, { cache: "no-store" });
  return readJson<LocalAppInfo[]>(response);
}

export async function fetchCurrentAppStatus(base: string): Promise<LocalAppStatus | null> {
  const response = await fetch(`${base}/api/local-apps/current`, { cache: "no-store" });
  return readJson<LocalAppStatus | null>(response);
}

export async function startApp(base: string, appId: string): Promise<LocalAppStatus> {
  const response = await fetch(`${base}/api/local-apps/${encodeURIComponent(appId)}/start`, {
    method: "POST",
  });
  return readJson<LocalAppStatus>(response);
}

export async function setupApp(base: string, appId: string): Promise<SetupStatus> {
  const response = await fetch(`${base}/api/local-apps/${encodeURIComponent(appId)}/setup`, {
    method: "POST",
  });
  return readJson<SetupStatus>(response);
}

export async function fetchSetupStatus(base: string, appId: string): Promise<SetupStatus> {
  const response = await fetch(`${base}/api/local-apps/${encodeURIComponent(appId)}/setup-status`, {
    cache: "no-store",
  });
  return readJson<SetupStatus>(response);
}

export async function stopCurrentApp(base: string): Promise<void> {
  const response = await fetch(`${base}/api/local-apps/current/stop`, {
    method: "POST",
  });
  if (!response.ok) {
    await readJson<unknown>(response);
  }
}

export async function restartApp(base: string, appId: string): Promise<LocalAppStatus> {
  const response = await fetch(`${base}/api/local-apps/${encodeURIComponent(appId)}/restart`, {
    method: "POST",
  });
  return readJson<LocalAppStatus>(response);
}

export async function waitForNoRunningApp(
  base: string,
  timeoutMs = 20_000,
): Promise<LocalAppStatus | null> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const status = await fetchCurrentAppStatus(base);
    if (!status || terminalStates.has(status.state)) {
      return status;
    }
    await delay(650);
  }

  throw new ApiError("Timed out while waiting for the current app to stop.");
}

export async function waitForAppSettle(
  base: string,
  appId: string,
  timeoutMs = 20_000,
): Promise<LocalAppStatus | null> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const status = await fetchCurrentAppStatus(base);
    if (status?.app.id === appId && status.state !== "starting") {
      return status;
    }
    await delay(650);
  }

  throw new ApiError(`Timed out while waiting for ${appId} to start.`);
}

export async function waitForSetupSettle(
  base: string,
  appId: string,
  onUpdate?: (status: SetupStatus) => void,
  timeoutMs = 20 * 60_000,
): Promise<SetupStatus> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const status = await fetchSetupStatus(base, appId);
    onUpdate?.(status);
    if (setupTerminalStates.has(status.state)) {
      return status;
    }
    await delay(1000);
  }

  throw new ApiError(`Timed out while setting up ${appId}.`);
}

export function resolveAppPageUrl(frontendUrl: string | null | undefined): string | null {
  if (!frontendUrl) {
    return null;
  }

  try {
    return new URL(frontendUrl).toString();
  } catch {
    return null;
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

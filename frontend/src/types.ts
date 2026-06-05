export type AppState = "starting" | "running" | "done" | "stopping" | "error";
export type SetupState = "unavailable" | "idle" | "needed" | "running" | "done" | "error" | "ready";
export type AppEnvironment = "shared" | "venv";

export interface SetupInfo {
  available: boolean;
  required: boolean;
  installed: boolean;
  state: SetupState;
  error?: string | null;
  logs: string[];
  environment?: AppEnvironment | null;
  python?: string | null;
  venvPath?: string | null;
  target?: string | null;
}

export interface SetupStatus {
  appId: string;
  state: "idle" | "running" | "done" | "error";
  startedAt?: number | null;
  finishedAt?: number | null;
  error?: string | null;
  logs: string[];
}

export interface LocalAppInfo {
  id: string;
  title: string;
  description: string;
  path: string;
  hasReadme: boolean;
  hasDescriptor: boolean;
  configured: boolean;
  startable: boolean;
  command: string[];
  python?: string | null;
  environment?: AppEnvironment | null;
  venv?: string | null;
  module?: string | null;
  args?: string[];
  installed: boolean;
  setup: SetupInfo;
  setupCommand?: string[];
  setupHint?: string;
  frontendUrl?: string | null;
  healthUrl?: string | null;
}

export interface LocalAppStatus {
  app: LocalAppInfo;
  state: AppState;
  pid?: number | null;
  startedAt?: number | null;
  returnCode?: number | null;
  error?: string | null;
}

export interface ApiSettings {
  managerUrl: string;
  useDevProxy: boolean;
}

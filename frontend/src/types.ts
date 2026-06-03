export type AppState = "starting" | "running" | "done" | "stopping" | "error";

export interface LocalAppInfo {
  id: string;
  title: string;
  description: string;
  path: string;
  hasReadme: boolean;
  hasDescriptor: boolean;
  startable: boolean;
  command: string[];
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

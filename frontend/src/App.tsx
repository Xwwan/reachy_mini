import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  FileWarning,
  Loader2,
  Play,
  Power,
  RefreshCw,
  RotateCw,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  apiBase,
  fetchCurrentAppStatus,
  fetchLocalApps,
  normalizeManagerUrl,
  resolveAppPageUrl,
  restartApp,
  startApp,
  stopCurrentApp,
  waitForAppSettle,
  waitForNoRunningApp,
} from "./api";
import type { ApiSettings, LocalAppInfo, LocalAppStatus } from "./types";

const managerUrlStorageKey = "reachy-mini-switcher-manager-url";
const proxyStorageKey = "reachy-mini-switcher-use-manager-proxy";
const defaultManagerUrl = "http://127.0.0.1:8787";

function displayState(status: LocalAppStatus | null): string {
  return status?.state ?? "idle";
}

function stateClass(state: string): string {
  if (state === "running") {
    return "status-good";
  }
  if (state === "starting" || state === "stopping") {
    return "status-working";
  }
  if (state === "error") {
    return "status-error";
  }
  return "status-idle";
}

function App() {
  const [settings, setSettings] = useState<ApiSettings>(() => {
    const savedManagerUrl = localStorage.getItem(managerUrlStorageKey);
    const savedProxy = localStorage.getItem(proxyStorageKey);
    return {
      managerUrl: normalizeManagerUrl(savedManagerUrl ?? defaultManagerUrl),
      useDevProxy: savedProxy ? savedProxy === "true" : import.meta.env.DEV,
    };
  });
  const [managerUrlDraft, setManagerUrlDraft] = useState(settings.managerUrl);
  const [apps, setApps] = useState<LocalAppInfo[]>([]);
  const [currentStatus, setCurrentStatus] = useState<LocalAppStatus | null>(null);
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [iframeNonce, setIframeNonce] = useState(0);

  const base = useMemo(
    () => apiBase(settings.managerUrl, settings.useDevProxy),
    [settings.managerUrl, settings.useDevProxy],
  );

  const selectedApp = useMemo(
    () => apps.find((app) => app.id === selectedAppId),
    [apps, selectedAppId],
  );
  const runningApp = currentStatus?.app;
  const activeApp = selectedApp ?? runningApp;
  const appPageUrl = resolveAppPageUrl(activeApp?.frontendUrl);
  const connectionState = error ? "attention" : "ready";
  const actionLocked = busyAction !== null;

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const [nextApps, status] = await Promise.all([
        fetchLocalApps(base),
        fetchCurrentAppStatus(base),
      ]);
      setApps(nextApps);
      setCurrentStatus(status);
      setLastUpdated(new Date());

      if (!selectedAppId && status) {
        setSelectedAppId(status.app.id);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to reach the App Manager.");
      setApps([]);
    }
  }, [base, selectedAppId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (actionLocked) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const status = await fetchCurrentAppStatus(base);
        setCurrentStatus(status);
        setLastUpdated(new Date());
      } catch {
        // Explicit refresh surfaces connection errors; polling stays quiet.
      }
    }, 2500);

    return () => window.clearInterval(timer);
  }, [actionLocked, base]);

  function applySettings() {
    const normalized = normalizeManagerUrl(managerUrlDraft);
    const next = { ...settings, managerUrl: normalized };
    setSettings(next);
    setManagerUrlDraft(normalized);
    localStorage.setItem(managerUrlStorageKey, normalized);
    localStorage.setItem(proxyStorageKey, String(next.useDevProxy));
  }

  function toggleProxy(value: boolean) {
    const next = { ...settings, useDevProxy: value };
    setSettings(next);
    localStorage.setItem(proxyStorageKey, String(value));
  }

  async function runWithBusy(label: string, action: () => Promise<void>) {
    setBusyAction(label);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Action failed.");
    } finally {
      setBusyAction(null);
    }
  }

  async function switchToApp(app: LocalAppInfo) {
    setSelectedAppId(app.id);

    await runWithBusy(`Starting ${app.title}`, async () => {
      const started = await startApp(base, app.id);
      setCurrentStatus(started);

      const settled = await waitForAppSettle(base, app.id);
      setCurrentStatus(settled);
      setIframeNonce((value) => value + 1);
      await refresh();
    });
  }

  async function stopApp() {
    await runWithBusy("Stopping app", async () => {
      await stopCurrentApp(base);
      await waitForNoRunningApp(base);
      setCurrentStatus(null);
      await refresh();
    });
  }

  async function restartActiveApp() {
    const appId = activeApp?.id ?? currentStatus?.app.id;
    if (!appId) {
      return;
    }

    await runWithBusy("Restarting app", async () => {
      const restarted = await restartApp(base, appId);
      setCurrentStatus(restarted);
      const settled = await waitForAppSettle(base, appId);
      setCurrentStatus(settled);
      setIframeNonce((value) => value + 1);
      await refresh();
    });
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Reachy Mini Lite</p>
          <h1>App Switcher</h1>
        </div>
        <div className={`connection-pill ${connectionState}`}>
          {error ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
          <span>{error ? "Needs attention" : "Manager ready"}</span>
        </div>
      </header>

      <section className="daemon-bar" aria-label="App Manager connection">
        <label className="daemon-field">
          <span>App Manager</span>
          <input
            value={managerUrlDraft}
            onChange={(event) => setManagerUrlDraft(event.target.value)}
            onBlur={applySettings}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                applySettings();
              }
            }}
          />
        </label>
        <label className="proxy-toggle">
          <input
            type="checkbox"
            checked={settings.useDevProxy}
            onChange={(event) => toggleProxy(event.target.checked)}
          />
          <span>Dev proxy</span>
        </label>
        <button className="icon-button" onClick={() => void refresh()} disabled={actionLocked} title="Refresh">
          <RefreshCw size={18} />
        </button>
      </section>

      {error && (
        <div className="notice error">
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      )}

      <section className="workspace">
        <aside className="app-list" aria-label="Apps">
          <div className="section-heading">
            <h2>Apps</h2>
            <span>{apps.length}</span>
          </div>

          {apps.length === 0 ? (
            <div className="empty-state">No apps found. Is the App Manager running?</div>
          ) : (
            <ul>
              {apps.map((app) => {
                const isRunning = currentStatus?.app.id === app.id;
                const isSelected = selectedAppId === app.id || (!selectedAppId && isRunning);
                const appState = isRunning ? currentStatus?.state ?? "running" : "idle";

                return (
                  <li key={app.id}>
                    <button
                      className={`app-row ${isSelected ? "selected" : ""}`}
                      onClick={() => setSelectedAppId(app.id)}
                    >
                      <span className={`state-dot ${stateClass(appState)}`} />
                      <span className="app-row-main">
                        <span className="app-title">{app.title}</span>
                        <span className="app-subtitle">{app.path}</span>
                      </span>
                      <span className={`app-state ${stateClass(appState)}`}>{appState}</span>
                    </button>
                    <div className="app-row-actions">
                      <button
                        onClick={() => void switchToApp(app)}
                        disabled={actionLocked || !app.startable}
                        title={app.startable ? `Start ${app.title}` : "Add reachy-app.json with a command first"}
                      >
                        <Play size={16} />
                        <span>Start</span>
                      </button>
                      {!app.startable && (
                        <span className="app-hint">
                          <FileWarning size={14} />
                          descriptor needed
                        </span>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section className="current-panel" aria-label="Current app">
          <div className="current-header">
            <div>
              <p className="eyebrow">Current</p>
              <h2>{activeApp?.title ?? "No app selected"}</h2>
            </div>
            <div className={`app-state large ${stateClass(displayState(currentStatus))}`}>
              {busyAction ? (
                <>
                  <Loader2 size={16} className="spin" />
                  <span>{busyAction}</span>
                </>
              ) : (
                displayState(currentStatus)
              )}
            </div>
          </div>

          <div className="current-actions">
            <button
              className="primary-action"
              onClick={() => activeApp && void switchToApp(activeApp)}
              disabled={!activeApp || !activeApp.startable || actionLocked}
            >
              <Play size={18} />
              <span>Start</span>
            </button>
            <button onClick={() => void stopApp()} disabled={!currentStatus || actionLocked}>
              <Square size={18} />
              <span>Stop</span>
            </button>
            <button onClick={() => void restartActiveApp()} disabled={!activeApp || actionLocked}>
              <RotateCw size={18} />
              <span>Restart</span>
            </button>
            {appPageUrl && (
              <a className="button-link" href={appPageUrl} target="_blank" rel="noreferrer">
                <ExternalLink size={18} />
                <span>Open</span>
              </a>
            )}
          </div>

          <div className="meta-grid">
            <div>
              <span>ID</span>
              <strong>{activeApp?.id ?? "-"}</strong>
            </div>
            <div>
              <span>Path</span>
              <strong>{activeApp?.path ?? "-"}</strong>
            </div>
            <div>
              <span>Process</span>
              <strong>{currentStatus?.pid ? `pid ${currentStatus.pid}` : "-"}</strong>
            </div>
            <div>
              <span>Updated</span>
              <strong>{lastUpdated ? lastUpdated.toLocaleTimeString() : "-"}</strong>
            </div>
          </div>

          {activeApp?.description && <p className="description">{activeApp.description}</p>}

          {activeApp?.setupHint && (
            <div className="notice info">
              <FileWarning size={18} />
              <span>{activeApp.setupHint}</span>
            </div>
          )}

          {currentStatus?.error && (
            <div className="notice error">
              <AlertCircle size={18} />
              <span>{currentStatus.error}</span>
            </div>
          )}

          <div className="app-frame-area">
            {appPageUrl ? (
              <iframe
                key={`${appPageUrl}-${iframeNonce}`}
                title={`${activeApp?.title ?? "Reachy app"} page`}
                src={appPageUrl}
              />
            ) : (
              <div className="no-frame">
                <Power size={30} />
                <h3>{activeApp ? "No app page" : "Select an app"}</h3>
                <p>
                  {activeApp
                    ? "This app has no frontendUrl in reachy-app.json. It can still run as a background app."
                    : "Choose an app from the list to start switching."}
                </p>
              </div>
            )}
          </div>
        </section>
      </section>
    </main>
  );
}

export default App;

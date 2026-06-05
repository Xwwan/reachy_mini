# Reachy Mini App Switcher Frontend Plan

## Understanding

We need a frontend in this repository's `frontend` directory that can display the robot apps and switch between them. Switching in the frontend must actually start and stop apps on the Reachy Mini daemon, not only change UI state.

For apps that expose their own web UI, the switcher should open the corresponding app page after the app starts.

The implementation should focus on the repository's `apps` and `frontend` directories, while using the existing Reachy Mini REST API as the robot-control boundary.

## Proposed Product Shape

Build a compact app manager web UI:

- A daemon connection bar for choosing the daemon base URL.
- A list of installed apps returned by the daemon.
- Start, stop, restart, and refresh controls.
- A current-app status panel.
- An embedded app viewport for apps with `extra.custom_app_url`.
- A fallback state panel for apps that run without a web UI.
- Clear errors for daemon unreachable, app start failure, app stop failure, and app page unavailable.

## Technical Approach

Use a standalone frontend project under `frontend`.

Recommended stack:

- Vite
- React
- TypeScript
- Plain CSS modules or regular CSS

The browser talks to the daemon REST API:

- `GET /api/apps/list-available/installed`
- `GET /api/apps/current-app-status`
- `POST /api/apps/start-app/{app_name}`
- `POST /api/apps/stop-current-app`
- `POST /api/apps/restart-current-app`

During local development, Vite should proxy `/api` to a configured daemon URL, defaulting to `http://localhost:8000`.

At runtime, the frontend should also allow the user to set the daemon URL manually, for example:

- Lite: `http://localhost:8000`
- Wireless: `http://reachy-mini.local:8000`
- Manual IP: `http://<robot-ip>:8000`

## App Page Handling

The daemon's installed-app response may contain:

```json
{
  "name": "example_app",
  "extra": {
    "custom_app_url": "http://0.0.0.0:8042"
  }
}
```

When an app with `custom_app_url` starts successfully:

1. Parse the URL.
2. Replace `0.0.0.0` or localhost-like hostnames with the daemon host selected in the frontend.
3. Show the resulting URL in an iframe.
4. Provide an "open in new tab" action as a fallback.

If there is no `custom_app_url`, show the app as running without an embedded page.

## Switching Flow

When the user selects a different app:

1. Lock app controls to prevent duplicate clicks.
2. Read current running app status.
3. If another app is running, call `POST /api/apps/stop-current-app`.
4. Wait until `GET /api/apps/current-app-status` returns `null` or a terminal state.
5. Start the selected app with `POST /api/apps/start-app/{app_name}`.
6. Poll status until the app reaches `running`, `done`, or `error`.
7. If running and a `custom_app_url` exists, load its web UI.
8. Unlock controls and show the resulting state.

## Repository App Metadata

The robot-control source of truth should be the daemon's installed app list, because only daemon-installed apps can be started.

The local `apps/` directory can be used later for richer metadata, such as README descriptions, screenshots, categories, or local development install hints. If we need that in this iteration, add a build-time script that scans `../apps/*/README.md` into `frontend/public/apps-manifest.json`.

## Initial File Plan

Create these files:

- `frontend/package.json`
- `frontend/index.html`
- `frontend/vite.config.ts`
- `frontend/tsconfig.json`
- `frontend/src/main.tsx`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/styles.css`

Optional later:

- `frontend/scripts/build-apps-manifest.mjs`
- `frontend/public/apps-manifest.json`

## Clarifying Questions

Please answer these before implementation:

1. Should I use React + TypeScript + Vite, as proposed?
   - Answer: Yes.

2. Should the frontend list only daemon-installed apps, or should it also show local folders under `apps/` even if they are not installed and cannot be started yet?
   - Answer: 应用仅展示 `apps` 下的应用，因为这个项目虽然 fork 自 Reachy Mini 但仍是相对独立的项目，应用不会通过 daemon 下载，之后也会有我们自己写的应用加入。

3. For apps with their own frontend, do you prefer embedding them in an iframe inside the switcher, opening them in a new tab, or supporting both?
   - Answer: Supporting both.

4. What should the default daemon URL be for your main test environment: `http://localhost:8000`, `http://reachy-mini.local:8000`, or a fixed IP?
   - Answer: 我们只关心 Lite 版本，可用 `http://localhost:8000` 作为默认。Wireless 版本的东西其实都不用写。

## Revised Direction: Local App Manager API

The frontend should not depend on the official Reachy Mini daemon app registry
for app listing or switching. Instead, add a local App Manager API owned by this
project.

New runtime shape:

```text
Frontend
  -> local App Manager API
      -> scans ../apps
      -> starts/stops local app processes
          -> each app connects to the Reachy Mini daemon or its own backend as needed
```

The official Reachy Mini daemon remains useful for robot hardware control, but
it is no longer the frontend's application-management backend.

## Local App Descriptor

Each app directory should provide a `reachy-app.json` file:

```json
{
  "id": "reachy_dialogue_app",
  "title": "Reachy Dialogue App",
  "description": "Interaction API voice dialogue bridge for Reachy Mini.",
  "command": [
    "python",
    "-m",
    "reachy_dialogue_app.reachy_dialogue_app.main",
    "--robot-host",
    "127.0.0.1",
    "--spawn-daemon"
  ],
  "frontendUrl": "http://127.0.0.1:8042/",
  "healthUrl": "http://127.0.0.1:8042/api/app-mode",
  "stopSignal": "SIGINT",
  "env": {}
}
```

Apps without this descriptor can still be shown as local folders, but they are
not startable until they define a command.

## New API Surface

Add a local App Manager service under `frontend/server`:

- `GET /api/local-apps`
- `GET /api/local-apps/current`
- `POST /api/local-apps/{app_id}/start`
- `POST /api/local-apps/current/stop`
- `POST /api/local-apps/{app_id}/restart`

The manager allows one running app at a time. Starting another app stops the
current app first.

## Updated Frontend Assumptions

- Use React + TypeScript + Vite.
- List apps from the local App Manager API, which scans `../apps`.
- Support both iframe embedding and "open in new tab".
- Default App Manager API URL to the same origin during dev via Vite proxy.
- Keep Reachy daemon URL out of the app-switching path; individual app commands
  can still pass `--robot-host 127.0.0.1` or equivalent.

## Environment Management Update

The App Manager now supports two explicit runtime modes in `reachy-app.json`:

- `environment: "shared"` uses the Python executable from the already-activated
  project conda environment. Setup runs `python -m pip install ...` into that
  shared environment and Start is allowed without checking for a per-app `.venv`.
- `environment: "venv"` keeps the previous behavior: create/check the app's own
  `.venv`, disable Start until setup creates it, and run the app with that venv's
  Python.

Recommended workflow: first verify all common app dependencies in a temporary
conda environment with `python -m pip check`. Apps that pass can use `shared`;
apps with conflicts should stay on `venv`.

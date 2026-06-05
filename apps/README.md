# Local Reachy Mini Apps

This directory is the local install location for user-managed Reachy Mini apps.

The repository intentionally ignores everything under `apps/` except this file.
Clone, copy, or symlink apps here on each machine as needed.

Expected layout:

```text
apps/
  your_app/
    reachy-app.json
    ...
```

The frontend App Manager scans this directory for `reachy-app.json` files. Apps
without that descriptor may appear as local folders, but they are not startable.

For shared conda environments, use:

```json
{
  "environment": "shared",
  "python": "python",
  "module": "your_app.main",
  "setup": {
    "install": ["-e", "."]
  }
}
```

For isolated per-app environments, use:

```json
{
  "environment": "venv",
  "python": "python3",
  "venv": ".venv",
  "module": "your_app.main",
  "setup": {
    "install": ["-e", "."]
  }
}
```

Run the App Manager from the desired Python or conda environment before clicking
`Install deps` for shared-environment apps.

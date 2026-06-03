from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve_data_dir(instance_path: str | None) -> Path:
    """Resolve and ensure the data directory for persistence."""
    if instance_path:
        base = Path(instance_path)
    else:
        base = Path(__file__).parent
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def load_json(path: Path, default: Any) -> Any:
    """Load JSON from disk, returning default if missing or invalid."""
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    """Write JSON to disk."""
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

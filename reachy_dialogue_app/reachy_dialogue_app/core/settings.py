from __future__ import annotations

import threading
from typing import Any

from .http import _normalize_service_url


def _snapshot(
    settings: dict[str, Any], settings_lock: threading.Lock
) -> dict[str, Any]:
    with settings_lock:
        current = dict(settings)
    current["service_url"] = _normalize_service_url(current["service_url"])
    return current

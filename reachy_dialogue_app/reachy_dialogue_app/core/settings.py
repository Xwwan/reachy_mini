"""线程安全读取 UI 设置。"""

from __future__ import annotations

import threading
from typing import Any

from .http import _normalize_service_url


def _snapshot(
    settings: dict[str, Any], settings_lock: threading.Lock
) -> dict[str, Any]:
    """复制当前设置并规范化 service_url，避免调用方持锁做网络请求。"""

    with settings_lock:
        current = dict(settings)
    current["service_url"] = _normalize_service_url(current["service_url"])
    return current

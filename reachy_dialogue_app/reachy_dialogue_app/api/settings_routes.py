"""设置与健康检查路由。"""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import urljoin

import requests
from fastapi import FastAPI

from ..behavior import _public_behavior_config, _public_emoji_config
from ..core.http import _normalize_service_url
from ..core.settings import _snapshot
from .payloads import SettingsPayload


def _register_settings_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
    *,
    behavior_config: dict[str, Any] | None = None,
) -> None:
    """注册 settings/health/config 相关接口。"""

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        with settings_lock:
            return dict(settings)

    @app.post("/api/settings")
    def update_settings(payload: SettingsPayload) -> dict[str, Any]:
        with settings_lock:
            if payload.service_url is not None:
                settings["service_url"] = _normalize_service_url(payload.service_url)
            if payload.conversation_id is not None:
                settings["conversation_id"] = payload.conversation_id.strip()
            if payload.tts_sample_rate is not None:
                settings["tts_sample_rate"] = payload.tts_sample_rate
            return dict(settings)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            response = requests.get(
                urljoin(current["service_url"], "/healthz"),
                timeout=3,
            )
            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "service_url": current["service_url"],
            }
        except requests.RequestException as exc:
            return {
                "ok": False,
                "error": str(exc),
                "service_url": current["service_url"],
            }


    if behavior_config is not None:
        @app.get("/api/emoji-config")
        def get_emoji_config() -> dict[str, Any]:
            return _public_emoji_config(behavior_config)

        @app.get("/api/behavior-config")
        def get_behavior_config() -> dict[str, Any]:
            return _public_behavior_config(behavior_config)

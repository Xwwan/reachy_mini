"""设置与健康检查路由。"""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import urljoin

import requests
from fastapi import FastAPI, HTTPException

from ..behavior import _public_behavior_config, _public_emoji_config
from ..core.http import _normalize_service_url
from ..core.settings import _snapshot
from .payloads import DemoProfilePayload, SettingsPayload


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


    @app.get("/api/demo-profile")
    def get_demo_profile() -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            response = requests.get(
                urljoin(current["service_url"], "/demo/profile"),
                timeout=5,
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _json_or_demo_error(response)

    @app.post("/api/demo-profile")
    def update_demo_profile(payload: DemoProfilePayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            response = requests.post(
                urljoin(current["service_url"], "/demo/profile"),
                json={"profile": payload.profile},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _json_or_demo_error(response)


    if behavior_config is not None:
        @app.get("/api/emoji-config")
        def get_emoji_config() -> dict[str, Any]:
            return _public_emoji_config(behavior_config)

        @app.get("/api/behavior-config")
        def get_behavior_config() -> dict[str, Any]:
            return _public_behavior_config(behavior_config)


def _json_or_demo_error(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"demo profile response was not JSON: {response.text[:200]}",
        ) from exc
    if response.ok:
        return payload
    detail = payload.get("detail") or payload.get("message")
    error = payload.get("error")
    if detail is None and isinstance(error, dict):
        detail = error.get("message")
    raise HTTPException(status_code=response.status_code, detail=detail or payload)

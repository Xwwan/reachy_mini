"""HTTP/SSE 辅助函数。

这些函数供多个 FastAPI 路由复用：规范化外部服务地址、解析 requests 响应、
访问机器人 daemon 音量接口，以及生成 SSE frame。
"""

from __future__ import annotations

import json
from typing import Any

import requests
from fastapi import HTTPException

from .constants import DEFAULT_SERVICE_URL


def _normalize_service_url(value: str) -> str:
    """把空值补成默认服务地址，并确保末尾有斜杠。"""

    value = value.strip() or DEFAULT_SERVICE_URL
    return value.rstrip("/") + "/"


def _json_or_error(response: requests.Response) -> dict[str, Any]:
    """解析 JSON；HTTP 错误时抛出 FastAPI HTTPException。"""

    try:
        data = response.json()
    except ValueError:
        data = {"error": {"message": response.text}}
    if not response.ok:
        message = data.get("error", {}).get("message", response.text)
        raise HTTPException(status_code=response.status_code, detail=message)
    return data


def _reply_text_from_payload(data: dict[str, Any]) -> str:
    for key in ("reply", "response", "answer", "text"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _daemon_volume_request(
    reachy_mini: ReachyMini,
    method: str,
    endpoint: str,
    *,
    volume: int | None = None,
) -> dict[str, Any]:
    """代理访问 Reachy daemon 的音量接口。"""

    daemon_url = getattr(reachy_mini, "_daemon_http_url", "").rstrip("/")
    if not daemon_url:
        daemon_url = f"http://{reachy_mini.host}:{reachy_mini.port}"
    body = None
    if volume is not None:
        body = {"volume": max(0, min(100, int(volume)))}
    try:
        response = requests.request(
            method,
            daemon_url + endpoint,
            json=body,
            timeout=5,
        )
        return _json_or_error(response)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"音量接口不可用：{exc}",
        ) from exc


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    """把事件名和 JSON payload 编码成浏览器 EventSource 可读的帧。"""

    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

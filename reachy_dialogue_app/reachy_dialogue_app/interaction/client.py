"""Interaction API 的同步 HTTP 客户端。

Reachy Dialogue App 自身只负责机器人 IO 和本地 UI；LLM、ASR/TTS、记忆和
workflow 都由外部 Interaction 服务提供。本客户端把这些 HTTP/SSE 接口包装成
后端路由和自动语音状态机可以直接调用的方法。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin

import requests

from ..core.constants import DEFAULT_SERVICE_URL, INPUT_SAMPLE_RATE
from .sse import SseEvent, iter_sse_events
from .types import AudioFormat, InputMode, JsonDict, Workflow


@dataclass
class InteractionApiError(RuntimeError):
    """Interaction 服务返回错误时抛出的结构化异常。"""

    message: str
    status_code: int | None = None
    payload: JsonDict | None = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.status_code}: {self.message}"


class InteractionApiClient:
    """对 Interaction 服务的薄封装，保留 requests.Session 以复用连接。"""

    def __init__(
        self,
        service_url: str = DEFAULT_SERVICE_URL,
        *,
        session: requests.Session | None = None,
        request_timeout: float = 10.0,
        stream_timeout: tuple[float, float] = (10.0, 120.0),
    ) -> None:
        self.service_url = normalize_service_url(service_url)
        self.session = session or requests.Session()
        self.request_timeout = request_timeout
        self.stream_timeout = stream_timeout

    def with_service_url(self, service_url: str) -> "InteractionApiClient":
        return InteractionApiClient(
            service_url,
            session=self.session,
            request_timeout=self.request_timeout,
            stream_timeout=self.stream_timeout,
        )

    def health(self) -> JsonDict:
        response = self.session.get(
            self._url("/healthz"),
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def create_session(
        self,
        *,
        workflow: Workflow,
        conversation_id: str,
        input_mode: InputMode,
        tts_enabled: bool,
    ) -> JsonDict:
        response = self.session.post(
            self._url("/interaction/sessions"),
            json={
                "workflow": workflow,
                "conversation_id": conversation_id,
                "input_mode": input_mode,
                "tts_enabled": tts_enabled,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def get_session(self, interaction_session_id: str) -> JsonDict:
        response = self.session.get(
            self._url(
                "/interaction/sessions/"
                + quote(interaction_session_id, safe="")
            ),
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def list_runs(
        self,
        interaction_session_id: str,
        *,
        limit: int = 50,
    ) -> JsonDict:
        response = self.session.get(
            self._url(
                "/interaction/sessions/"
                + quote(interaction_session_id, safe="")
                + "/runs"
            ),
            params={"limit": int(limit)},
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def get_run(self, run_id: str) -> JsonDict:
        response = self.session.get(
            self._url("/interaction/runs/" + quote(run_id, safe="")),
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def text_stream(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        message: str,
        tts_enabled: bool,
    ) -> Iterable[SseEvent]:
        """发送文本消息并返回流式回复事件。"""

        response = self.session.post(
            self._url("/interaction/runs/text-stream"),
            json={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "message": message,
                "tts_enabled": tts_enabled,
            },
            stream=True,
            timeout=self.stream_timeout,
        )
        return self._iter_response_events(response)

    def live_start(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        sample_rate: int = INPUT_SAMPLE_RATE,
        channels: int = 1,
        audio_format: AudioFormat = "pcm",
    ) -> JsonDict:
        """创建实时语音输入会话，后续 chunk/transcript/finish 都使用其 id。"""

        response = self.session.post(
            self._url("/interaction/live/start"),
            json={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "sample_rate": int(sample_rate),
                "channels": int(channels),
                "audio_format": audio_format,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def live_chunk(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        live_session_id: str,
        audio_base64: str,
        is_final: bool = False,
    ) -> JsonDict:
        response = self.session.post(
            self._url("/interaction/live/chunk"),
            json={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
                "audio_base64": audio_base64,
                "is_final": is_final,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def live_transcript(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        live_session_id: str,
    ) -> JsonDict:
        response = self.session.get(
            self._url("/interaction/live/transcript"),
            params={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def live_finish_transcript(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        live_session_id: str,
    ) -> JsonDict:
        response = self.session.post(
            self._url("/interaction/live/finish-transcript"),
            json={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            },
            timeout=self.stream_timeout,
        )
        return json_or_error(response)

    def live_finish_stream(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        live_session_id: str,
        tts_enabled: bool,
    ) -> Iterable[SseEvent]:
        """结束实时语音输入，并以 SSE 形式获取最终回复。"""

        response = self.session.post(
            self._url("/interaction/live/finish-stream"),
            json={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
                "tts_enabled": tts_enabled,
            },
            stream=True,
            timeout=self.stream_timeout,
        )
        return self._iter_response_events(response)

    def live_abort(
        self,
        *,
        interaction_session_id: str,
        workflow: Workflow,
        live_session_id: str,
    ) -> JsonDict:
        response = self.session.post(
            self._url("/interaction/live/abort"),
            json={
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def playback_done(self, *, run_id: str, playback_key: str) -> JsonDict:
        response = self.session.post(
            self._url("/interaction/playback/done"),
            json={
                "run_id": run_id,
                "playback_key": playback_key,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def playback_error(
        self,
        *,
        run_id: str,
        playback_key: str,
        error: str,
    ) -> JsonDict:
        response = self.session.post(
            self._url("/interaction/playback/error"),
            json={
                "run_id": run_id,
                "playback_key": playback_key,
                "error": error,
            },
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def list_pending_followups(self) -> JsonDict:
        response = self.session.get(
            self._url("/followups/pending"),
            timeout=self.request_timeout,
        )
        return json_or_error(response)

    def followup_stream(
        self,
        *,
        conversation_id: str,
        tts_enabled: bool = False,
    ) -> Iterable[SseEvent]:
        response = self.session.get(
            self._url("/followups/stream"),
            params={
                "conversation_id": conversation_id,
                "tts_enabled": bool(tts_enabled),
            },
            stream=True,
            timeout=self.stream_timeout,
        )
        return self._iter_response_events(response)

    def run_followup(self, request_id: str) -> JsonDict:
        response = self.session.post(
            self._url("/followups/" + quote(request_id, safe="") + "/run"),
            json={},
            timeout=self.stream_timeout,
        )
        return json_or_error(response)

    def memory_curate(
        self,
        *,
        conversation_id: str,
        history_limit: int = 50,
    ) -> JsonDict:
        response = self.session.post(
            self._url("/memory/curate"),
            json={
                "conversation_id": conversation_id,
                "history_limit": int(history_limit),
            },
            timeout=self.stream_timeout,
        )
        return json_or_error(response)

    def memory_profile_refresh(self) -> JsonDict:
        response = self.session.post(
            self._url("/memory/profile/refresh"),
            json={},
            timeout=self.stream_timeout,
        )
        return json_or_error(response)

    def _iter_response_events(self, response: requests.Response) -> Iterable[SseEvent]:
        """统一处理 SSE HTTP 错误和事件解析。"""

        try:
            raise_for_error(response)
            yield from iter_sse_events(response)
        finally:
            response.close()

    def _url(self, path: str) -> str:
        return urljoin(self.service_url, path.lstrip("/"))


def normalize_service_url(value: str) -> str:
    """保证服务地址有协议和结尾斜杠，便于 urljoin。"""

    value = value.strip() or DEFAULT_SERVICE_URL
    return value.rstrip("/") + "/"


def json_or_error(response: requests.Response) -> JsonDict:
    """解析 JSON 响应；非 2xx 时转成 InteractionApiError。"""

    try:
        data: Any = response.json()
    except ValueError:
        data = {"error": {"message": response.text}}

    if not response.ok:
        payload = data if isinstance(data, dict) else {"value": data}
        raise InteractionApiError(
            message=error_message(payload, response.text),
            status_code=response.status_code,
            payload=payload,
        )

    if isinstance(data, dict):
        return data
    return {"value": data}


def raise_for_error(response: requests.Response) -> None:
    if response.ok:
        return
    try:
        data: Any = response.json()
    except ValueError:
        data = {"error": {"message": response.text}}
    payload = data if isinstance(data, dict) else {"value": data}
    raise InteractionApiError(
        message=error_message(payload, response.text),
        status_code=response.status_code,
        payload=payload,
    )


def error_message(payload: JsonDict, fallback: str) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    message = payload.get("message")
    if isinstance(message, str) and message:
        return message
    return fallback or "interaction API request failed"

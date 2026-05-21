from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

from ..core.constants import OUTPUT_SAMPLE_RATE


TEXT_TTS_SEGMENT_PUNCTUATION = "。！？!?；;\n"
TEXT_TTS_SEGMENT_MAX_CHARS = 80
TEXT_TTS_TAG_PATTERN = re.compile(
    r"\[\s*(?:emo|act|emotion|action|表情|动作)\s*[:：]\s*[^\]\r\n]+?\]"
)


def _payload_has_audio(payload: dict[str, Any]) -> bool:
    return any(
        isinstance(payload.get(key), str) and bool(payload[key])
        for key in ("audio_base64", "response_audio_base64", "tts_audio_base64")
    )


def _iter_text_tts_audio_payloads(
    reply: str,
    identity_payload: dict[str, Any],
    *,
    conversation_id: str,
    default_sample_rate: int,
    behavior_config: dict[str, Any],
    client_factory: Callable[[dict[str, Any]], Any] | None = None,
):
    tts_text = _prepare_tts_text(reply)
    if not tts_text.strip():
        return
    client = (client_factory or _build_text_tts_client)(behavior_config)
    stream_fn = getattr(client, "synthesize_stream", None)
    chunks = (
        stream_fn(tts_text)
        if callable(stream_fn)
        else [client.synthesize(tts_text)]
    )
    sample_rate = int(getattr(client, "sample_rate", default_sample_rate))
    base_payload = {
        "conversation_id": identity_payload.get("conversation_id") or conversation_id,
        "request_id": identity_payload.get("request_id", ""),
        "turn_id": (
            identity_payload.get("assistant_turn_id")
            or identity_payload.get("reply_turn_id")
            or identity_payload.get("turn_id", "")
        ),
        "assistant_turn_id": (
            identity_payload.get("assistant_turn_id")
            or identity_payload.get("reply_turn_id")
            or identity_payload.get("turn_id", "")
        ),
        "phase": "initial",
        "audio_format": "pcm",
        "sample_rate": sample_rate,
        "segment_index": 0,
    }
    for index, chunk in enumerate(chunks):
        if not chunk:
            continue
        data = dict(base_payload)
        data.update(
            {
                "audio_base64": base64.b64encode(chunk).decode("ascii"),
                "chunk_index": index,
            }
        )
        yield data


def _prepare_tts_text(text: str) -> str:
    return TEXT_TTS_TAG_PATTERN.sub("", text).strip()


def _build_text_tts_client(behavior_config: dict[str, Any]):
    config = _text_tts_config(behavior_config)
    api_key_env = _config_string(config, "api_key_env", "DASHSCOPE_API_KEY")
    api_key = os.environ.get("REACHY_DIALOGUE_TTS_API_KEY") or os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"文字 TTS 需要环境变量 {api_key_env} 或 REACHY_DIALOGUE_TTS_API_KEY"
        )
    return DashScopeRealtimeTtsClient(
        api_key=api_key,
        realtime_url=_config_string(
            config,
            "realtime_url",
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        ),
        model=_config_string(config, "model", "qwen3-tts-flash-realtime"),
        voice=_config_string(config, "voice", "Cherry"),
        sample_rate=_config_int(config, "sample_rate", OUTPUT_SAMPLE_RATE),
        timeout_seconds=_config_float(config, "timeout_seconds", 60.0),
    )


def _text_tts_config(behavior_config: dict[str, Any]) -> dict[str, Any]:
    audio_config = behavior_config.get("audio")
    if isinstance(audio_config, dict) and isinstance(audio_config.get("tts"), dict):
        return audio_config["tts"]
    tts_config = behavior_config.get("tts")
    if isinstance(tts_config, dict):
        return tts_config
    return {}


def _config_string(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _config_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _config_float(config: dict[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a positive number")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    raise ValueError(f"{key} must be a positive number")


@dataclass
class DashScopeRealtimeTtsClient:
    api_key: str
    realtime_url: str
    model: str
    voice: str
    sample_rate: int
    timeout_seconds: float

    def synthesize(self, text: str) -> bytes:
        return b"".join(self.synthesize_stream(text))

    def synthesize_stream(self, text: str):
        if not isinstance(text, str) or not text.strip():
            return
        websocket = _load_websocket_client()
        url = f"{self.realtime_url}?{urlencode({'model': self.model})}"
        socket = websocket.create_connection(
            url,
            timeout=self.timeout_seconds,
            header=[f"Authorization: Bearer {self.api_key}"],
        )
        try:
            yield from self._iter_session_audio(socket, text)
        finally:
            socket.close()

    def _iter_session_audio(self, socket: Any, text: str):
        while True:
            raw = socket.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
            msg_type = message.get("type")
            if msg_type == "session.created":
                socket.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "voice": self.voice,
                                "response_format": "pcm",
                                "sample_rate": self.sample_rate,
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            if msg_type == "session.updated":
                socket.send(
                    json.dumps(
                        {"type": "input_text_buffer.append", "text": text},
                        ensure_ascii=False,
                    )
                )
                socket.send(json.dumps({"type": "input_text_buffer.commit"}))
                continue
            if msg_type == "response.audio.delta":
                delta = message.get("delta")
                if isinstance(delta, str):
                    yield base64.b64decode(delta)
                continue
            if msg_type == "response.done":
                return
            if msg_type == "error":
                raise RuntimeError(f"DashScope TTS error: {message}")


def _load_websocket_client() -> Any:
    try:
        import websocket
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError("文字 TTS 需要安装 websocket-client") from exc
    return websocket

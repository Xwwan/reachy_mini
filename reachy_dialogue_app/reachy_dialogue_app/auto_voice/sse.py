from __future__ import annotations

import base64
import json
from typing import Any

import requests


def audio_duration_from_payload(data: dict[str, Any]) -> float:
    audio_base64 = data.get("audio_base64")
    if not isinstance(audio_base64, str) or not audio_base64:
        return 0.0
    sample_rate = int(data.get("sample_rate") or data.get("audio_sample_rate") or 24000)
    try:
        audio_bytes = base64.b64decode(audio_base64)
    except Exception:
        return 0.0
    return len(audio_bytes) / max(1.0, 2.0 * float(sample_rate))


def iter_sse_events(response: requests.Response):
    if not response.ok:
        json_or_error(response)
    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                yield event, decode_sse_json("\n".join(data_lines))
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
    if data_lines:
        yield event, decode_sse_json("\n".join(data_lines))


def decode_sse_json(payload: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}
    if isinstance(data, dict):
        return data
    return {"value": data}


def json_or_error(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        if response.ok:
            return {}
        raise RuntimeError(response.text or response.reason) from exc
    if not response.ok:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise RuntimeError(str(detail or data or response.reason))
    if isinstance(data, dict):
        return data
    return {"value": data}

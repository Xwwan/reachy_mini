"""Interaction 服务 SSE 工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import requests

from .types import JsonDict


@dataclass(frozen=True)
class SseEvent:
    """解析后的单个 Server-Sent Event。"""

    event: str
    data: JsonDict


def iter_sse_events(response: requests.Response) -> Iterable[SseEvent]:
    """把 requests 流响应解析成 SseEvent 迭代器。"""

    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(chunk_size=8192, decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                yield SseEvent(event=event, data=decode_sse_json("\n".join(data_lines)))
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
        yield SseEvent(event=event, data=decode_sse_json("\n".join(data_lines)))


def decode_sse_json(payload: str) -> JsonDict:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}
    if isinstance(data, dict):
        return data
    return {"value": data}


def sse_frame(event: str, data: JsonDict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

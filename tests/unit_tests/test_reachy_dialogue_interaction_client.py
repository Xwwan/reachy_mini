from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_dialogue_app.reachy_dialogue_app.interaction import (
    InteractionApiClient,
    InteractionApiError,
    SseEvent,
    iter_sse_events,
)
from reachy_dialogue_app.reachy_dialogue_app.interaction.client import json_or_error
from reachy_dialogue_app.reachy_dialogue_app import main as dialogue_main


class FakeResponse:
    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        payload=None,
        text: str = "",
        lines: list[str] | None = None,
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self.payload = {} if payload is None else payload
        self.text = text
        self.lines = lines or []
        self.closed = False
        self.json_called = False

    def json(self):
        self.json_called = True
        return self.payload

    def iter_lines(self, chunk_size=1, decode_unicode=True):
        yield from self.lines

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response


def test_iter_sse_events_decodes_json_payloads() -> None:
    response = FakeResponse(
        lines=[
            "event: meta",
            'data: {"run_id":"irun_1"}',
            "",
            "event: delta",
            'data: {"delta":"hi"}',
            "",
        ]
    )

    events = list(iter_sse_events(response))  # type: ignore[arg-type]

    assert [(event.event, event.data) for event in events] == [
        ("meta", {"run_id": "irun_1"}),
        ("delta", {"delta": "hi"}),
    ]


def test_text_stream_posts_to_interaction_endpoint_without_json_preload() -> None:
    response = FakeResponse(
        lines=[
            "event: done",
            'data: {"run_id":"irun_1","playback_key":"pb_1"}',
            "",
        ]
    )
    session = FakeSession(response)
    client = InteractionApiClient(
        "http://backend.test",
        session=session,  # type: ignore[arg-type]
    )

    events = list(
        client.text_stream(
            interaction_session_id="isess_1",
            workflow="chat",
            message="hello",
            tts_enabled=True,
        )
    )

    assert session.calls == [
        (
            "POST",
            "http://backend.test/interaction/runs/text-stream",
            {
                "json": {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "message": "hello",
                    "tts_enabled": True,
                },
                "stream": True,
                "timeout": (10.0, 120.0),
            },
        )
    ]
    assert events[0].data["playback_key"] == "pb_1"
    assert response.json_called is False
    assert response.closed is True


def test_live_chunk_posts_required_interaction_fields() -> None:
    response = FakeResponse(payload={"ok": True, "accepted_bytes": 5120})
    session = FakeSession(response)
    client = InteractionApiClient(
        "http://backend.test/",
        session=session,  # type: ignore[arg-type]
    )

    result = client.live_chunk(
        interaction_session_id="isess_1",
        workflow="onboarding",
        live_session_id="live_1",
        audio_base64="AAAA",
    )

    assert result == {"ok": True, "accepted_bytes": 5120}
    assert session.calls[0][1] == "http://backend.test/interaction/live/chunk"
    assert session.calls[0][2]["json"] == {
        "interaction_session_id": "isess_1",
        "workflow": "onboarding",
        "live_session_id": "live_1",
        "audio_base64": "AAAA",
        "is_final": False,
    }


def test_json_or_error_uses_contract_error_message() -> None:
    response = FakeResponse(
        ok=False,
        status_code=409,
        payload={"error": {"message": "state machine rejected this run"}},
        text="fallback",
    )

    with pytest.raises(InteractionApiError) as exc_info:
        json_or_error(response)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert exc_info.value.message == "state machine rejected this run"


def test_interaction_session_route_creates_backend_session() -> None:
    fake_client = FakeInteractionRouteClient()
    app = FastAPI()
    settings = {
        "service_url": "http://backend.test",
        "conversation_id": "default-conversation",
        "tts_sample_rate": 24000,
    }
    dialogue_main._register_interaction_routes(
        app,
        settings,
        dialogue_main.threading.Lock(),
        behavior_config={"enabled": False},
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )

    response = TestClient(app).post(
        "/api/interaction/session",
        json={
            "workflow": "chat",
            "input_mode": "text",
            "tts_enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["interaction_session_id"] == "isess_1"
    assert fake_client.create_session_calls == [
        {
            "workflow": "chat",
            "conversation_id": "default-conversation",
            "input_mode": "text",
            "tts_enabled": True,
        }
    ]


def test_interaction_text_stream_route_proxies_new_backend_events() -> None:
    fake_client = FakeInteractionRouteClient(
        text_events=[
            SseEvent("meta", {"run_id": "irun_1"}),
            SseEvent("delta", {"delta": "你"}),
            SseEvent(
                "audio",
                {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "run_id": "irun_1",
                    "playback_key": "chat-tts-irun_1",
                    "audio_base64": "AAAA",
                    "sample_rate": 24000,
                    "chunk_index": 0,
                },
            ),
            SseEvent("state_delta", {"stage": 1}),
            SseEvent(
                "done",
                {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "run_id": "irun_1",
                    "reply": "你好",
                },
            ),
        ]
    )
    app = FastAPI()
    settings = {
        "service_url": "http://backend.test",
        "conversation_id": "default-conversation",
        "tts_sample_rate": 24000,
    }
    dialogue_main._register_interaction_routes(
        app,
        settings,
        dialogue_main.threading.Lock(),
        behavior_config={"enabled": False},
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )

    response = TestClient(app).post(
        "/api/interaction/text-stream",
        json={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "message": "hello",
            "tts_enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.text
    assert "event: meta" in body
    assert "event: delta" in body
    assert "event: audio" in body
    assert "event: state_delta" in body
    assert "event: done" in body
    assert "event: playback_done" in body
    assert '"skipped": true' in body
    assert fake_client.text_stream_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "message": "hello",
            "tts_enabled": True,
        }
    ]


class FakeInteractionRouteClient:
    def __init__(self, text_events: list[SseEvent] | None = None) -> None:
        self.text_events = text_events or []
        self.create_session_calls: list[dict] = []
        self.text_stream_calls: list[dict] = []

    def create_session(
        self,
        *,
        workflow: str,
        conversation_id: str,
        input_mode: str,
        tts_enabled: bool,
    ) -> dict:
        self.create_session_calls.append(
            {
                "workflow": workflow,
                "conversation_id": conversation_id,
                "input_mode": input_mode,
                "tts_enabled": tts_enabled,
            }
        )
        return {
            "interaction_session_id": "isess_1",
            "workflow": workflow,
            "conversation_id": conversation_id,
            "input_mode": input_mode,
            "tts_enabled": tts_enabled,
        }

    def text_stream(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        message: str,
        tts_enabled: bool,
    ):
        self.text_stream_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "message": message,
                "tts_enabled": tts_enabled,
            }
        )
        yield from self.text_events

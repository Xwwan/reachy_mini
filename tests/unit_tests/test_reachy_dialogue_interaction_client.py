from __future__ import annotations

import pytest

from reachy_dialogue_app.reachy_dialogue_app.interaction import (
    InteractionApiClient,
    InteractionApiError,
    iter_sse_events,
)
from reachy_dialogue_app.reachy_dialogue_app.interaction.client import json_or_error


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

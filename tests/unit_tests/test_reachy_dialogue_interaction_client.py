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


def test_live_finish_transcript_posts_to_interaction_endpoint() -> None:
    response = FakeResponse(
        payload={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "transcript": "最终识别文本",
            "is_final": True,
        }
    )
    session = FakeSession(response)
    client = InteractionApiClient(
        "http://backend.test/",
        session=session,  # type: ignore[arg-type]
    )

    result = client.live_finish_transcript(
        interaction_session_id="isess_1",
        workflow="chat",
        live_session_id="live_1",
    )

    assert result["transcript"] == "最终识别文本"
    assert session.calls == [
        (
            "POST",
            "http://backend.test/interaction/live/finish-transcript",
            {
                "json": {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "live_session_id": "live_1",
                },
                "timeout": (10.0, 120.0),
            },
        )
    ]


def test_followup_and_memory_client_methods_use_contract_routes() -> None:
    response = FakeResponse(
        payload={
            "pending": [
                {
                    "request_id": "req_1",
                    "conversation_id": "conv",
                    "status": "retrieval_completed",
                }
            ]
        }
    )
    session = FakeSession(response)
    client = InteractionApiClient(
        "http://backend.test/",
        session=session,  # type: ignore[arg-type]
    )

    pending = client.list_pending_followups()
    run_result = client.run_followup("req_1")
    curate_result = client.memory_curate(conversation_id="conv", history_limit=25)
    profile_result = client.memory_profile_refresh()

    assert pending["pending"][0]["request_id"] == "req_1"
    assert run_result == response.payload
    assert curate_result == response.payload
    assert profile_result == response.payload
    assert session.calls == [
        (
            "GET",
            "http://backend.test/followups/pending",
            {"timeout": 10.0},
        ),
        (
            "POST",
            "http://backend.test/followups/req_1/run",
            {"json": {}, "timeout": (10.0, 120.0)},
        ),
        (
            "POST",
            "http://backend.test/memory/curate",
            {
                "json": {"conversation_id": "conv", "history_limit": 25},
                "timeout": (10.0, 120.0),
            },
        ),
        (
            "POST",
            "http://backend.test/memory/profile/refresh",
            {"json": {}, "timeout": (10.0, 120.0)},
        ),
    ]


def test_followup_stream_client_uses_sse_contract() -> None:
    response = FakeResponse(
        lines=[
            "event: followup",
            'data: {"request_id":"req_1","reply":"补充一句"}',
            "",
        ]
    )
    session = FakeSession(response)
    client = InteractionApiClient(
        "http://backend.test/",
        session=session,  # type: ignore[arg-type]
    )

    events = list(
        client.followup_stream(
            conversation_id="conv",
            tts_enabled=True,
        )
    )

    assert events == [SseEvent("followup", {"request_id": "req_1", "reply": "补充一句"})]
    assert session.calls == [
        (
            "GET",
            "http://backend.test/followups/stream",
            {
                "params": {"conversation_id": "conv", "tts_enabled": True},
                "stream": True,
                "timeout": (10.0, 120.0),
            },
        )
    ]
    assert response.closed is True


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


def test_interaction_session_run_debug_routes_proxy_backend_methods() -> None:
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
    client = TestClient(app)

    session_response = client.get("/api/interaction/session/isess_1")
    runs_response = client.get("/api/interaction/session/isess_1/runs", params={"limit": 5})
    run_response = client.get("/api/interaction/runs/irun_1")

    assert session_response.status_code == 200
    assert session_response.json()["interaction_session_id"] == "isess_1"
    assert runs_response.status_code == 200
    assert runs_response.json()["runs"][0]["run_id"] == "irun_1"
    assert run_response.status_code == 200
    assert run_response.json()["playback_status"] == "done"
    assert fake_client.get_session_calls == ["isess_1"]
    assert fake_client.list_runs_calls == [
        {"interaction_session_id": "isess_1", "limit": 5}
    ]
    assert fake_client.get_run_calls == ["irun_1"]


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


def test_interaction_live_routes_proxy_new_backend_live_methods() -> None:
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
    client = TestClient(app)

    start_response = client.post(
        "/api/interaction/live/start",
        json={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "sample_rate": 16000,
            "channels": 1,
            "audio_format": "pcm",
        },
    )
    chunk_response = client.post(
        "/api/interaction/live/chunk",
        json={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "audio_base64": "AAAA",
            "is_final": False,
        },
    )
    transcript_response = client.get(
        "/api/interaction/live/transcript",
        params={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
        },
    )
    abort_response = client.post(
        "/api/interaction/live/abort",
        json={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
        },
    )

    assert start_response.status_code == 200
    assert start_response.json()["live_session_id"] == "live_1"
    assert chunk_response.status_code == 200
    assert chunk_response.json()["accepted_bytes"] == 2
    assert transcript_response.status_code == 200
    assert transcript_response.json()["transcript"] == "hello"
    assert abort_response.status_code == 200
    assert abort_response.json() == {"ok": True}
    assert fake_client.live_start_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "sample_rate": 16000,
            "channels": 1,
            "audio_format": "pcm",
        }
    ]
    assert fake_client.live_chunk_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "audio_base64": "AAAA",
            "is_final": False,
        }
    ]
    assert fake_client.live_transcript_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
        }
    ]
    assert fake_client.live_abort_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
        }
    ]


def test_interaction_live_finish_stream_route_proxies_events() -> None:
    fake_client = FakeInteractionRouteClient(
        live_events=[
            SseEvent(
                "transcript",
                {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "live_session_id": "live_1",
                    "transcript": "hello",
                    "is_final": True,
                },
            ),
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
            SseEvent(
                "done",
                {
                    "interaction_session_id": "isess_1",
                    "workflow": "chat",
                    "run_id": "irun_1",
                    "transcript": "hello",
                    "reply": "hi",
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
        "/api/interaction/live/finish-stream",
        json={
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "tts_enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.text
    assert "event: transcript" in body
    assert "event: audio" in body
    assert "event: done" in body
    assert "event: playback_done" in body
    assert '"skipped": true' in body
    assert fake_client.live_finish_stream_calls == [
        {
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "live_session_id": "live_1",
            "tts_enabled": True,
        }
    ]


def test_followup_memory_routes_proxy_backend_contracts() -> None:
    fake_client = FakeInteractionRouteClient(
        followup_events=[
            SseEvent(
                "followup",
                {
                    "conversation_id": "conv",
                    "request_id": "req_1",
                    "followup_turn_id": "turn_f1",
                    "followup_type": "supplement",
                    "reply": "补充一句",
                },
            ),
            SseEvent(
                "audio",
                {
                    "conversation_id": "conv",
                    "request_id": "req_1",
                    "followup_turn_id": "turn_f1",
                    "turn_id": "turn_f1",
                    "audio_base64": "AAAA",
                    "sample_rate": 24000,
                    "chunk_index": 0,
                },
            ),
            SseEvent(
                "followup_done",
                {
                    "conversation_id": "conv",
                    "request_id": "req_1",
                    "followup_turn_id": "turn_f1",
                    "turn_id": "turn_f1",
                },
            ),
        ],
    )
    app = FastAPI()
    settings = {
        "service_url": "http://backend.test",
        "conversation_id": "conv",
        "tts_sample_rate": 24000,
    }
    dialogue_main._register_followup_memory_routes(
        app,
        settings,
        dialogue_main.threading.Lock(),
        behavior_config={"enabled": False},
        client_factory=lambda service_url: fake_client,  # type: ignore[arg-type]
    )
    client = TestClient(app)

    pending_response = client.get("/api/followups/pending")
    run_response = client.post("/api/followups/req_1/run")
    stream_response = client.get(
        "/api/followups/stream",
        params={"conversation_id": "conv", "tts_enabled": "true"},
    )
    curate_response = client.post(
        "/api/memory/curate",
        json={"conversation_id": "conv", "history_limit": 25},
    )
    profile_response = client.post("/api/memory/profile/refresh")

    assert pending_response.status_code == 200
    assert pending_response.json()["pending"][0]["request_id"] == "req_1"
    assert run_response.status_code == 200
    assert run_response.json()["decision"] == "followup"
    assert stream_response.status_code == 200
    assert "event: followup" in stream_response.text
    assert "event: audio" in stream_response.text
    assert "event: followup_done" in stream_response.text
    assert "event: playback_done" in stream_response.text
    assert '"skipped": true' in stream_response.text
    assert curate_response.status_code == 200
    assert curate_response.json()["conversation_id"] == "conv"
    assert profile_response.status_code == 200
    assert profile_response.json()["should_update"] is False
    assert fake_client.followup_stream_calls == [
        {"conversation_id": "conv", "tts_enabled": True}
    ]
    assert fake_client.memory_curate_calls == [
        {"conversation_id": "conv", "history_limit": 25}
    ]


class FakeInteractionRouteClient:
    def __init__(
        self,
        text_events: list[SseEvent] | None = None,
        live_events: list[SseEvent] | None = None,
        followup_events: list[SseEvent] | None = None,
    ) -> None:
        self.text_events = text_events or []
        self.live_events = live_events or []
        self.followup_events = followup_events or []
        self.create_session_calls: list[dict] = []
        self.get_session_calls: list[str] = []
        self.list_runs_calls: list[dict] = []
        self.get_run_calls: list[str] = []
        self.text_stream_calls: list[dict] = []
        self.live_start_calls: list[dict] = []
        self.live_chunk_calls: list[dict] = []
        self.live_transcript_calls: list[dict] = []
        self.live_finish_stream_calls: list[dict] = []
        self.live_abort_calls: list[dict] = []
        self.followup_stream_calls: list[dict] = []
        self.memory_curate_calls: list[dict] = []

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

    def get_session(self, interaction_session_id: str) -> dict:
        self.get_session_calls.append(interaction_session_id)
        return {
            "interaction_session_id": interaction_session_id,
            "workflow": "chat",
            "conversation_id": "default-conversation",
            "status": "active",
        }

    def list_runs(
        self,
        interaction_session_id: str,
        *,
        limit: int,
    ) -> dict:
        self.list_runs_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "limit": limit,
            }
        )
        return {
            "interaction_session_id": interaction_session_id,
            "runs": [
                {
                    "run_id": "irun_1",
                    "interaction_session_id": interaction_session_id,
                    "workflow": "chat",
                    "status": "completed",
                    "playback_status": "done",
                }
            ],
        }

    def get_run(self, run_id: str) -> dict:
        self.get_run_calls.append(run_id)
        return {
            "run_id": run_id,
            "interaction_session_id": "isess_1",
            "workflow": "chat",
            "status": "completed",
            "playback_status": "done",
            "reply": "你好",
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

    def live_start(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        sample_rate: int,
        channels: int,
        audio_format: str,
    ) -> dict:
        self.live_start_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "sample_rate": sample_rate,
                "channels": channels,
                "audio_format": audio_format,
            }
        )
        return {
            "interaction_session_id": interaction_session_id,
            "workflow": workflow,
            "live_session_id": "live_1",
            "session_id": "live_1",
            "sample_rate": sample_rate,
            "channels": channels,
            "audio_format": audio_format,
        }

    def live_chunk(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
        audio_base64: str,
        is_final: bool,
    ) -> dict:
        self.live_chunk_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
                "audio_base64": audio_base64,
                "is_final": is_final,
            }
        )
        return {"ok": True, "accepted_bytes": 2}

    def live_transcript(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
    ) -> dict:
        self.live_transcript_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            }
        )
        return {
            "interaction_session_id": interaction_session_id,
            "workflow": workflow,
            "live_session_id": live_session_id,
            "transcript": "hello",
            "is_final": False,
            "error": None,
        }

    def live_finish_stream(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
        tts_enabled: bool,
    ):
        self.live_finish_stream_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
                "tts_enabled": tts_enabled,
            }
        )
        yield from self.live_events

    def live_abort(
        self,
        *,
        interaction_session_id: str,
        workflow: str,
        live_session_id: str,
    ) -> dict:
        self.live_abort_calls.append(
            {
                "interaction_session_id": interaction_session_id,
                "workflow": workflow,
                "live_session_id": live_session_id,
            }
        )
        return {"ok": True}

    def list_pending_followups(self) -> dict:
        return {
            "pending": [
                {
                    "request_id": "req_1",
                    "conversation_id": "conv",
                    "status": "retrieval_completed",
                }
            ]
        }

    def run_followup(self, request_id: str) -> dict:
        return {
            "request_id": request_id,
            "conversation_id": "conv",
            "decision": "followup",
            "followup_type": "supplement",
            "reply": "补充一句",
        }

    def followup_stream(
        self,
        *,
        conversation_id: str,
        tts_enabled: bool,
    ):
        self.followup_stream_calls.append(
            {
                "conversation_id": conversation_id,
                "tts_enabled": tts_enabled,
            }
        )
        yield from self.followup_events

    def memory_curate(
        self,
        *,
        conversation_id: str,
        history_limit: int,
    ) -> dict:
        self.memory_curate_calls.append(
            {
                "conversation_id": conversation_id,
                "history_limit": history_limit,
            }
        )
        return {
            "conversation_id": conversation_id,
            "operations": [],
            "applied": [],
        }

    def memory_profile_refresh(self) -> dict:
        return {
            "should_update": False,
            "patch": None,
            "reason": "no change",
            "new_profile": None,
        }

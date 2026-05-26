from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from ..auto_voice import AutoVoiceManager
from ..core.constants import DEFAULT_CONVERSATION_ID
from ..core.http import _sse_frame
from ..core.settings import _snapshot
from .common import _validate_workflow
from .payloads import AutoVoiceChunkPayload, AutoVoiceStartPayload, AutoVoiceStopPayload


def _register_auto_voice_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
    manager: AutoVoiceManager,
    *,
    allow_robot: bool,
) -> None:
    @app.get("/api/auto-voice/config")
    def auto_voice_config() -> dict[str, Any]:
        return {
            "model_path": str(manager.model_path),
            "model_exists": manager.model_path.exists(),
            "input_gain": manager.config.input_gain,
            "local_chunk_queue_size": manager.config.local_chunk_queue_size,
            "robot_poll_seconds": manager.config.robot_poll_seconds,
            "transcript_poll_seconds": manager.config.transcript_poll_seconds,
            "service_timeout_seconds": manager.config.service_timeout_seconds,
            "wake_gate": manager.config.wake_gate.__dict__,
            "vad": manager.config.vad.__dict__,
            "allow_robot": allow_robot,
        }

    @app.post("/api/auto-voice/start")
    def auto_voice_start(payload: AutoVoiceStartPayload) -> dict[str, Any]:
        if payload.input_mode not in {"local", "robot"}:
            raise HTTPException(
                status_code=422,
                detail="input_mode must be 'local' or 'robot'.",
            )
        if payload.input_mode == "robot" and not allow_robot:
            raise HTTPException(
                status_code=422,
                detail="web-only 模式不能使用机器人麦克风自动对话。",
            )
        current = _snapshot(settings, settings_lock)
        conversation_id = (
            payload.conversation_id or current["conversation_id"] or DEFAULT_CONVERSATION_ID
        ).strip()
        try:
            session = manager.start(
                mode=payload.input_mode,  # type: ignore[arg-type]
                conversation_id=conversation_id,
                tts_enabled=payload.tts_enabled,
                workflow=_validate_workflow(payload.workflow),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        snapshot = session.snapshot()
        return {
            "session_id": snapshot.session_id,
            "input_mode": snapshot.mode,
            "workflow": session.workflow,
            "interaction_session_id": session.interaction_session_id,
            "state": snapshot.state,
            "conversation_id": snapshot.conversation_id,
            "tts_enabled": snapshot.tts_enabled,
            "gate_state": snapshot.gate_state,
            "wake_gate_enabled": snapshot.wake_gate_enabled,
            "vad": manager.config.vad.__dict__,
            "wake_gate": manager.config.wake_gate.__dict__,
            "model_path": str(manager.model_path),
        }

    @app.post("/api/auto-voice/chunk")
    def auto_voice_chunk(payload: AutoVoiceChunkPayload) -> dict[str, Any]:
        try:
            session = manager.get(payload.session_id)
            accepted = session.submit_pcm16_base64(
                payload.audio_base64,
                payload.sample_rate,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="auto voice session not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "ok": True,
            "accepted": accepted,
            "session_id": payload.session_id,
            "state": session.snapshot().state,
        }

    @app.get("/api/auto-voice/events")
    def auto_voice_events(session_id: str) -> StreamingResponse:
        try:
            session = manager.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="auto voice session not found") from exc

        def event_stream():
            try:
                for event, data in session.event_stream():
                    yield _sse_frame(event, data)
            finally:
                # EventSource disconnects should not leave a hidden listener alive forever.
                pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/auto-voice/state")
    def auto_voice_state(session_id: str) -> dict[str, Any]:
        try:
            return manager.snapshot(session_id).__dict__
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="auto voice session not found") from exc

    @app.post("/api/auto-voice/stop")
    def auto_voice_stop(payload: AutoVoiceStopPayload) -> dict[str, Any]:
        try:
            manager.stop(payload.session_id)
        except KeyError:
            return {"ok": True, "session_id": payload.session_id, "already_stopped": True}
        return {"ok": True, "session_id": payload.session_id}

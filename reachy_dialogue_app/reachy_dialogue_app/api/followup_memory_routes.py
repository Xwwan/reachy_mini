from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from ..audio.playback import (
    NullPlaybackSink,
    RobotAudioPlaybackScheduler,
    RobotPlaybackSink,
    _new_playback_key,
    _optional_int,
    _playback_metadata_from_payload,
)
from ..behavior import (
    _behavior_result_payload,
    _first_ok_module_key,
    _module_config,
    _trigger_behaviors_from_text,
)
from ..core.constants import DEFAULT_CONVERSATION_ID
from ..core.http import _reply_text_from_payload, _sse_frame
from ..core.settings import _snapshot
from ..interaction import InteractionApiClient, InteractionApiError
from .common import _interaction_http_exception, _required_string
from .payloads import MemoryCuratePayload


def _register_followup_memory_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
    *,
    behavior_config: dict[str, Any],
    playback_scheduler: RobotAudioPlaybackScheduler | None = None,
    client_factory: Callable[[str], InteractionApiClient] = InteractionApiClient,
) -> None:
    playback_sink = (
        RobotPlaybackSink(playback_scheduler)
        if playback_scheduler is not None
        else NullPlaybackSink()
    )

    @app.get("/api/followups/pending")
    def list_pending_followups() -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).list_pending_followups()
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.post("/api/followups/{request_id}/run")
    def run_followup(request_id: str) -> dict[str, Any]:
        request_id = _required_string(request_id, "request_id")
        current = _snapshot(settings, settings_lock)
        try:
            data = client_factory(current["service_url"]).run_followup(request_id)
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

        reply = _reply_text_from_payload(data)
        behavior_results = _trigger_behaviors_from_text(reply, behavior_config)
        if behavior_results:
            data = dict(data)
            data["behavior_results"] = [
                _behavior_result_payload(result) for result in behavior_results
            ]
        return data

    @app.get("/api/followups/stream")
    def followup_stream(
        conversation_id: str,
        tts_enabled: bool = False,
    ) -> StreamingResponse:
        current = _snapshot(settings, settings_lock)
        conversation_id = _required_string(conversation_id, "conversation_id")

        def event_stream():
            behavior_results_by_key: dict[str, list[Any]] = {}
            try:
                client = client_factory(current["service_url"])
                for item in client.followup_stream(
                    conversation_id=conversation_id,
                    tts_enabled=tts_enabled,
                ):
                    event = item.event
                    data = item.data
                    if event == "followup":
                        playback_key = _playback_metadata_from_payload(
                            data,
                            _new_playback_key("followup"),
                        ).playback_key
                        behavior_results = _trigger_behaviors_from_text(
                            _reply_text_from_payload(data),
                            behavior_config,
                        )
                        behavior_results_by_key[playback_key] = behavior_results
                        yield _sse_frame(event, data)
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                        continue

                    if event == "audio":
                        audio_base64 = data.get("audio_base64")
                        if (
                            playback_sink.active
                            and isinstance(audio_base64, str)
                            and audio_base64
                        ):
                            metadata = _playback_metadata_from_payload(
                                data,
                                _new_playback_key("followup"),
                            )
                            playback_sink.enqueue_audio(
                                metadata.playback_key,
                                audio_base64=audio_base64,
                                sample_rate=int(
                                    data.get("sample_rate")
                                    or data.get("audio_sample_rate")
                                    or current["tts_sample_rate"]
                                ),
                                chunk_index=_optional_int(data.get("chunk_index")),
                                segment_index=_optional_int(
                                    data.get("segment_index")
                                ),
                                playback_metadata=metadata,
                            )
                        yield _sse_frame(event, data)
                        continue

                    if event == "followup_done":
                        playback_done: threading.Event | None = None
                        metadata = _playback_metadata_from_payload(
                            data,
                            _new_playback_key("followup"),
                        )
                        if playback_sink.active:
                            behavior_results = behavior_results_by_key.pop(
                                metadata.playback_key,
                                [],
                            )
                            playback_done = threading.Event()
                            playback_sink.complete(
                                metadata.playback_key,
                                action_signal=_first_ok_module_key(
                                    behavior_results,
                                    "action",
                                ),
                                action_config=_module_config(
                                    behavior_config,
                                    "action",
                                ),
                                done_event=playback_done,
                                playback_metadata=metadata,
                            )
                        yield _sse_frame(event, data)
                        if playback_done is not None:
                            playback_done.wait(timeout=120)
                            yield _sse_frame(
                                "playback_done",
                                {
                                    "ok": True,
                                    "playback_key": metadata.playback_key,
                                    "phase": "followup",
                                },
                            )
                        elif not playback_sink.active:
                            yield _sse_frame(
                                "playback_done",
                                {
                                    "ok": True,
                                    "skipped": True,
                                    "phase": "followup",
                                },
                            )
                        continue

                    yield _sse_frame(event, data)
            except InteractionApiError as exc:
                yield _sse_frame(
                    "error",
                    {
                        "message": exc.message,
                        "status_code": exc.status_code,
                    },
                )
            except Exception as exc:
                yield _sse_frame(
                    "error",
                    {"message": str(exc) or exc.__class__.__name__},
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/memory/curate")
    def memory_curate(payload: MemoryCuratePayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        conversation_id = (
            payload.conversation_id
            or current["conversation_id"]
            or DEFAULT_CONVERSATION_ID
        ).strip()
        try:
            return client_factory(current["service_url"]).memory_curate(
                conversation_id=conversation_id,
                history_limit=payload.history_limit,
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.post("/api/memory/profile/refresh")
    def memory_profile_refresh() -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).memory_profile_refresh()
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

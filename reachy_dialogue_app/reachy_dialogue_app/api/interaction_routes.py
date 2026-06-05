from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
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
    BehaviorTriggerTracker,
    _behavior_result_payload,
    _first_ok_module_key,
    _module_config,
)
from ..core.constants import DEFAULT_CONVERSATION_ID
from ..core.http import _reply_text_from_payload, _sse_frame
from ..core.settings import _snapshot
from ..interaction import InteractionApiClient, InteractionApiError
from .common import (
    _interaction_http_exception,
    _required_string,
    _validate_input_mode,
    _validate_workflow,
)
from .payloads import (
    InteractionLiveAbortPayload,
    InteractionLiveChunkPayload,
    InteractionLiveFinishStreamPayload,
    InteractionLiveStartPayload,
    InteractionSessionPayload,
    InteractionTextStreamPayload,
)


def _register_interaction_routes(
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

    def submit_behavior_actions(behavior_results: list[Any]) -> None:
        if not playback_sink.active:
            return
        playback_sink.submit_action(
            action_signal=_first_ok_module_key(behavior_results, "action"),
            action_config=_module_config(behavior_config, "action"),
        )

    @app.post("/api/interaction/session")
    def create_interaction_session(
        payload: InteractionSessionPayload,
    ) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        conversation_id = (
            payload.conversation_id
            or current["conversation_id"]
            or DEFAULT_CONVERSATION_ID
        ).strip()
        if not conversation_id:
            conversation_id = DEFAULT_CONVERSATION_ID
        try:
            return client_factory(current["service_url"]).create_session(
                workflow=_validate_workflow(payload.workflow),
                conversation_id=conversation_id,
                input_mode=_validate_input_mode(payload.input_mode),
                tts_enabled=payload.tts_enabled,
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.get("/api/interaction/session/{interaction_session_id}")
    def get_interaction_session(interaction_session_id: str) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).get_session(
                _required_string(interaction_session_id, "interaction_session_id")
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.get("/api/interaction/session/{interaction_session_id}/runs")
    def list_interaction_runs(
        interaction_session_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).list_runs(
                _required_string(interaction_session_id, "interaction_session_id"),
                limit=limit,
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.get("/api/interaction/runs/{run_id}")
    def get_interaction_run(run_id: str) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).get_run(
                _required_string(run_id, "run_id")
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.post("/api/interaction/text-stream")
    def interaction_text_stream(
        payload: InteractionTextStreamPayload,
    ) -> StreamingResponse:
        current = _snapshot(settings, settings_lock)
        workflow = _validate_workflow(payload.workflow)
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="文本不能为空。")
        interaction_session_id = payload.interaction_session_id.strip()
        if not interaction_session_id:
            raise HTTPException(
                status_code=422,
                detail="interaction_session_id is required.",
            )

        def event_stream():
            behavior_tracker = BehaviorTriggerTracker(behavior_config)
            playback_key: str | None = None
            playback_completed = False
            fallback_playback_key = _new_playback_key("interaction-text")
            try:
                client = client_factory(current["service_url"])
                for item in client.text_stream(
                    interaction_session_id=interaction_session_id,
                    workflow=workflow,
                    message=message,
                    tts_enabled=payload.tts_enabled,
                ):
                    event = item.event
                    data = item.data
                    if event == "audio":
                        audio_base64 = data.get("audio_base64")
                        if (
                            playback_sink.active
                            and isinstance(audio_base64, str)
                            and audio_base64
                        ):
                            metadata = _playback_metadata_from_payload(
                                data,
                                playback_key or fallback_playback_key,
                            )
                            playback_key = playback_sink.enqueue_audio(
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

                    if event == "delta":
                        behavior_results = behavior_tracker.trigger_from_fragment(
                            str(data.get("delta") or "")
                        )
                        submit_behavior_actions(behavior_results)
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                        yield _sse_frame(event, data)
                        continue

                    if event == "done":
                        reply = _reply_text_from_payload(data)
                        behavior_results = behavior_tracker.trigger_from_text(reply)
                        submit_behavior_actions(behavior_results)
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                        playback_done: threading.Event | None = None
                        if playback_sink.active and playback_key:
                            metadata = _playback_metadata_from_payload(
                                data,
                                playback_key,
                            )
                            playback_done = threading.Event()
                            playback_sink.complete(
                                playback_key,
                                done_event=playback_done,
                                playback_metadata=metadata,
                            )
                            playback_completed = True
                        yield _sse_frame(event, data)
                        if playback_done is not None:
                            playback_done.wait(timeout=120)
                            yield _sse_frame(
                                "playback_done",
                                {"ok": True, "playback_key": playback_key},
                            )
                        elif not playback_sink.active:
                            yield _sse_frame(
                                "playback_done",
                                {"ok": True, "skipped": True},
                            )
                        return

                    yield _sse_frame(event, data)
                    if event == "error":
                        if playback_sink.active:
                            playback_sink.abort(
                                playback_key or fallback_playback_key
                            )
                        return
            except InteractionApiError as exc:
                if not playback_completed and playback_sink.active:
                    playback_sink.abort(playback_key or fallback_playback_key)
                yield _sse_frame(
                    "error",
                    {
                        "message": exc.message,
                        "status_code": exc.status_code,
                    },
                )
            except Exception as exc:
                if not playback_completed and playback_sink.active:
                    playback_sink.abort(playback_key or fallback_playback_key)
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

    @app.post("/api/interaction/live/start")
    def interaction_live_start(payload: InteractionLiveStartPayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        interaction_session_id = payload.interaction_session_id.strip()
        if not interaction_session_id:
            raise HTTPException(
                status_code=422,
                detail="interaction_session_id is required.",
            )
        if payload.audio_format != "pcm":
            raise HTTPException(status_code=422, detail="audio_format must be 'pcm'.")
        try:
            return client_factory(current["service_url"]).live_start(
                interaction_session_id=interaction_session_id,
                workflow=_validate_workflow(payload.workflow),
                sample_rate=payload.sample_rate,
                channels=payload.channels,
                audio_format="pcm",
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.post("/api/interaction/live/chunk")
    def interaction_live_chunk(payload: InteractionLiveChunkPayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).live_chunk(
                interaction_session_id=_required_string(
                    payload.interaction_session_id,
                    "interaction_session_id",
                ),
                workflow=_validate_workflow(payload.workflow),
                live_session_id=_required_string(
                    payload.live_session_id,
                    "live_session_id",
                ),
                audio_base64=payload.audio_base64,
                is_final=payload.is_final,
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.get("/api/interaction/live/transcript")
    def interaction_live_transcript(
        interaction_session_id: str,
        live_session_id: str,
        workflow: str = "chat",
    ) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).live_transcript(
                interaction_session_id=_required_string(
                    interaction_session_id,
                    "interaction_session_id",
                ),
                workflow=_validate_workflow(workflow),
                live_session_id=_required_string(live_session_id, "live_session_id"),
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.post("/api/interaction/live/abort")
    def interaction_live_abort(payload: InteractionLiveAbortPayload) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        try:
            return client_factory(current["service_url"]).live_abort(
                interaction_session_id=_required_string(
                    payload.interaction_session_id,
                    "interaction_session_id",
                ),
                workflow=_validate_workflow(payload.workflow),
                live_session_id=_required_string(
                    payload.live_session_id,
                    "live_session_id",
                ),
            )
        except InteractionApiError as exc:
            raise _interaction_http_exception(exc) from exc

    @app.post("/api/interaction/live/finish-stream")
    def interaction_live_finish_stream(
        payload: InteractionLiveFinishStreamPayload,
    ) -> StreamingResponse:
        current = _snapshot(settings, settings_lock)
        workflow = _validate_workflow(payload.workflow)
        interaction_session_id = _required_string(
            payload.interaction_session_id,
            "interaction_session_id",
        )
        live_session_id = _required_string(payload.live_session_id, "live_session_id")

        def event_stream():
            behavior_tracker = BehaviorTriggerTracker(behavior_config)
            playback_key: str | None = None
            playback_completed = False
            fallback_playback_key = _new_playback_key("interaction-live")
            try:
                client = client_factory(current["service_url"])
                for item in client.live_finish_stream(
                    interaction_session_id=interaction_session_id,
                    workflow=workflow,
                    live_session_id=live_session_id,
                    tts_enabled=payload.tts_enabled,
                ):
                    event = item.event
                    data = item.data
                    if event == "audio":
                        audio_base64 = data.get("audio_base64")
                        if (
                            playback_sink.active
                            and isinstance(audio_base64, str)
                            and audio_base64
                        ):
                            metadata = _playback_metadata_from_payload(
                                data,
                                playback_key or fallback_playback_key,
                            )
                            playback_key = playback_sink.enqueue_audio(
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

                    if event == "delta":
                        behavior_results = behavior_tracker.trigger_from_fragment(
                            str(data.get("delta") or "")
                        )
                        submit_behavior_actions(behavior_results)
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                        yield _sse_frame(event, data)
                        continue

                    if event == "done":
                        reply = _reply_text_from_payload(data)
                        behavior_results = behavior_tracker.trigger_from_text(reply)
                        submit_behavior_actions(behavior_results)
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                        playback_done: threading.Event | None = None
                        if playback_sink.active and playback_key:
                            metadata = _playback_metadata_from_payload(
                                data,
                                playback_key,
                            )
                            playback_done = threading.Event()
                            playback_sink.complete(
                                playback_key,
                                done_event=playback_done,
                                playback_metadata=metadata,
                            )
                            playback_completed = True
                        yield _sse_frame(event, data)
                        if playback_done is not None:
                            playback_done.wait(timeout=120)
                            yield _sse_frame(
                                "playback_done",
                                {"ok": True, "playback_key": playback_key},
                            )
                        elif not playback_sink.active:
                            yield _sse_frame(
                                "playback_done",
                                {"ok": True, "skipped": True},
                            )
                        return

                    yield _sse_frame(event, data)
                    if event == "error":
                        if playback_sink.active:
                            playback_sink.abort(
                                playback_key or fallback_playback_key
                            )
                        return
            except InteractionApiError as exc:
                if not playback_completed and playback_sink.active:
                    playback_sink.abort(playback_key or fallback_playback_key)
                yield _sse_frame(
                    "error",
                    {
                        "message": exc.message,
                        "status_code": exc.status_code,
                    },
                )
            except Exception as exc:
                if not playback_completed and playback_sink.active:
                    playback_sink.abort(playback_key or fallback_playback_key)
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

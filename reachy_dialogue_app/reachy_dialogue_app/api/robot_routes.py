from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from reachy_mini import ReachyMini

from ..audio.playback import (
    RobotAudioPlaybackScheduler,
    _new_playback_key,
    _optional_int,
    _playback_metadata_from_payload,
)
from ..audio.robot_mic import LiveTranscript, RobotMicPlaybackTester, RobotMicRecorder
from ..behavior import (
    BehaviorTriggerTracker,
    _behavior_result_payload,
    _first_ok_module_key,
    _module_config,
)
from ..core.http import _daemon_volume_request, _sse_frame
from ..core.settings import _snapshot
from .common import _validate_workflow
from .payloads import (
    RobotMicInteractionFinishStreamPayload,
    RobotMicInteractionStartPayload,
    VolumePayload,
)


def _register_robot_routes(
    app: FastAPI,
    settings: dict[str, Any],
    settings_lock: threading.Lock,
    *,
    reachy_mini: ReachyMini,
    recorder: RobotMicRecorder,
    playback_tester: RobotMicPlaybackTester,
    playback_scheduler: RobotAudioPlaybackScheduler,
    behavior_config: dict[str, Any],
) -> None:
    def submit_behavior_actions(behavior_results: list[Any]) -> None:
        playback_scheduler.submit_action(
            action_signal=_first_ok_module_key(behavior_results, "action"),
            action_config=_module_config(behavior_config, "action"),
        )

    @app.get("/api/app-mode")
    def app_mode() -> dict[str, Any]:
        return {"web_only": False}

    @app.get("/api/audio-volume")
    def get_audio_volume() -> dict[str, Any]:
        return {
            "speaker": _daemon_volume_request(
                reachy_mini,
                "GET",
                "/api/volume/current",
            ),
            "microphone": _daemon_volume_request(
                reachy_mini,
                "GET",
                "/api/volume/microphone/current",
            ),
        }

    @app.post("/api/audio-volume/speaker")
    def set_speaker_volume(payload: VolumePayload) -> dict[str, Any]:
        return _daemon_volume_request(
            reachy_mini,
            "POST",
            "/api/volume/set",
            volume=payload.volume,
        )

    @app.post("/api/audio-volume/microphone")
    def set_microphone_volume(payload: VolumePayload) -> dict[str, Any]:
        return _daemon_volume_request(
            reachy_mini,
            "POST",
            "/api/volume/microphone/set",
            volume=payload.volume,
        )

    @app.post("/api/robot-mic/start-interaction")
    def start_robot_mic_interaction(
        payload: RobotMicInteractionStartPayload,
    ) -> dict[str, Any]:
        current = _snapshot(settings, settings_lock)
        if playback_tester.get_level().is_recording:
            raise HTTPException(
                status_code=409,
                detail="机器人麦克风回放测试正在录音，请先停止测试。",
            )
        interaction_session_id = payload.interaction_session_id.strip()
        if not interaction_session_id:
            raise HTTPException(
                status_code=422,
                detail="interaction_session_id is required.",
            )
        try:
            recorder.start_interaction(
                service_url=current["service_url"],
                interaction_session_id=interaction_session_id,
                workflow=_validate_workflow(payload.workflow),
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "ok": True,
            "interaction_session_id": interaction_session_id,
            "workflow": _validate_workflow(payload.workflow),
            "sample_rate": reachy_mini.media.get_input_audio_samplerate(),
            "channels": reachy_mini.media.get_input_channels(),
        }

    @app.post("/api/robot-mic/finish-interaction-stream")
    def finish_robot_mic_interaction_stream(
        payload: RobotMicInteractionFinishStreamPayload,
    ) -> StreamingResponse:
        current = _snapshot(settings, settings_lock)
        try:
            recording, session = recorder.stop_interaction_for_stream()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        def event_stream():
            behavior_tracker = BehaviorTriggerTracker(behavior_config)
            audio_sample_rate = int(current["tts_sample_rate"])
            fallback_playback_key = _new_playback_key("robot-mic-interaction")
            playback_key: str | None = None
            playback_completed = False
            try:
                yield _sse_frame(
                    "recording",
                    {
                        "audio_format": "pcm",
                        "sample_rate": recording.sample_rate,
                        "channels": recording.channels,
                        "duration_seconds": recording.duration_seconds,
                        "rms": recording.rms,
                        "peak": recording.peak,
                        "byte_count": recording.byte_count,
                    },
                )
                yield _sse_frame("debug", recorder.debug_snapshot())
                for item in session.finish_stream(tts_enabled=payload.tts_enabled):
                    event = str(item.get("event") or "message")
                    data = item.get("data") or {}
                    if event == "audio":
                        audio_base64 = data.get("audio_base64")
                        audio_sample_rate = int(
                            data.get("sample_rate")
                            or data.get("audio_sample_rate")
                            or audio_sample_rate
                        )
                        if isinstance(audio_base64, str) and audio_base64:
                            metadata = _playback_metadata_from_payload(
                                data,
                                playback_key or fallback_playback_key,
                            )
                            playback_key = playback_scheduler.enqueue_audio(
                                metadata.playback_key,
                                audio_base64=audio_base64,
                                sample_rate=audio_sample_rate,
                                chunk_index=_optional_int(
                                    data.get("chunk_index")
                                ),
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
                        recorder.final_response = dict(data)
                        recorder.live_transcript = LiveTranscript(
                            text=str(data.get("transcript") or ""),
                            is_final=True,
                            error=None,
                        )
                        behavior_results = behavior_tracker.trigger_from_text(
                            str(data.get("reply") or "")
                        )
                        submit_behavior_actions(behavior_results)
                        for result in behavior_results:
                            yield _sse_frame(
                                "behavior",
                                _behavior_result_payload(result),
                            )
                        playback_done: threading.Event | None = None
                        if playback_key:
                            metadata = _playback_metadata_from_payload(
                                data,
                                playback_key,
                            )
                            playback_done = threading.Event()
                            playback_scheduler.complete(
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
                        continue

                    yield _sse_frame(event, data)
                    if event == "error":
                        playback_scheduler.abort(
                            playback_key or fallback_playback_key
                        )
                        return
            except Exception as exc:
                if not playback_completed:
                    playback_scheduler.abort(playback_key or fallback_playback_key)
                yield _sse_frame(
                    "error",
                    {"message": str(exc) or exc.__class__.__name__},
                )
            finally:
                if not playback_completed:
                    playback_scheduler.abort(playback_key or fallback_playback_key)
                recorder.finish_reply_processing(session)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/robot-mic/level")
    def get_robot_mic_level() -> dict[str, Any]:
        level = recorder.get_level()
        return {
            "is_recording": level.is_recording,
            "duration_seconds": level.duration_seconds,
            "rms": level.rms,
            "peak": level.peak,
            "level": level.level,
        }

    @app.get("/api/robot-mic/transcript")
    def get_robot_mic_transcript() -> dict[str, Any]:
        transcript = recorder.get_transcript()
        return {
            "text": transcript.text,
            "is_final": transcript.is_final,
            "error": transcript.error,
        }

    @app.get("/api/robot-mic/debug")
    def get_robot_mic_debug() -> dict[str, Any]:
        return recorder.debug_snapshot()

    @app.post("/api/robot-mic/playback-test/start")
    def start_robot_mic_playback_test() -> dict[str, Any]:
        recorder_state = recorder.debug_snapshot()
        if recorder_state["is_recording"] or recorder_state["is_processing_reply"]:
            raise HTTPException(
                status_code=409,
                detail="语音对话正在录音或回复中，请等当前流程结束后再测试回放。",
            )
        try:
            playback_tester.start()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "ok": True,
            "sample_rate": reachy_mini.media.get_input_audio_samplerate(),
            "channels": reachy_mini.media.get_input_channels(),
        }

    @app.post("/api/robot-mic/playback-test/stop")
    def stop_robot_mic_playback_test() -> dict[str, Any]:
        try:
            recording = playback_tester.stop()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        playback_done = threading.Event()
        playback_scheduler.submit_complete(
            audio_base64=recording.audio_base64,
            audio_sample_rate=recording.sample_rate,
            done_event=playback_done,
        )
        playback_timeout = min(120.0, max(5.0, recording.duration_seconds + 5.0))
        playback_finished = playback_done.wait(timeout=playback_timeout)
        return {
            "ok": True,
            "audio_format": "pcm",
            "sample_rate": recording.sample_rate,
            "channels": recording.channels,
            "duration_seconds": recording.duration_seconds,
            "rms": recording.rms,
            "peak": recording.peak,
            "byte_count": recording.byte_count,
            "playback_finished": playback_finished,
        }

    @app.get("/api/robot-mic/playback-test/level")
    def get_robot_mic_playback_test_level() -> dict[str, Any]:
        level = playback_tester.get_level()
        return {
            "is_recording": level.is_recording,
            "duration_seconds": level.duration_seconds,
            "rms": level.rms,
            "peak": level.peak,
            "level": level.level,
        }

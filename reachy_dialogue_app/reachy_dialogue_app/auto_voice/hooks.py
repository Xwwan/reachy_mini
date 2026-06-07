from __future__ import annotations

import threading
from typing import Any, Callable

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
from ..core.constants import OUTPUT_SAMPLE_RATE


def _auto_voice_stream_hook_factory(
    playback_scheduler: RobotAudioPlaybackScheduler | None,
    behavior_config: dict[str, Any],
) -> Callable[
    [str],
    Callable[
        [str, dict[str, Any]],
        tuple[list[tuple[str, dict[str, Any]]], threading.Event | None],
    ],
]:
    playback_sink = (
        RobotPlaybackSink(playback_scheduler)
        if playback_scheduler is not None
        else NullPlaybackSink()
    )

    def factory(session_id: str):
        active_playback_key: str | None = None
        active_fallback_playback_key: str | None = None
        behavior_tracker = BehaviorTriggerTracker(behavior_config)

        def current_fallback_playback_key() -> str:
            nonlocal active_fallback_playback_key
            if active_fallback_playback_key is None:
                active_fallback_playback_key = _new_playback_key(
                    f"auto-voice-{session_id}"
                )
            return active_fallback_playback_key

        def reset_playback_group() -> None:
            nonlocal active_playback_key, active_fallback_playback_key
            active_playback_key = None
            active_fallback_playback_key = None

        def submit_behavior_actions(behavior_results: list[Any]) -> None:
            if not playback_sink.active:
                return
            playback_sink.submit_action(
                action_signal=_first_ok_module_key(behavior_results, "action"),
                action_config=_module_config(behavior_config, "action"),
            )

        def hook(
            event: str,
            data: dict[str, Any],
        ) -> tuple[list[tuple[str, dict[str, Any]]], threading.Event | None]:
            nonlocal active_playback_key
            extras: list[tuple[str, dict[str, Any]]] = []
            if event == "audio":
                audio_base64 = data.get("audio_base64")
                if (
                    playback_sink.active
                    and isinstance(audio_base64, str)
                    and audio_base64
                ):
                    playback_metadata = _playback_metadata_from_payload(
                        data,
                        active_playback_key or current_fallback_playback_key(),
                    )
                    key = playback_metadata.playback_key
                    active_playback_key = playback_sink.enqueue_audio(
                        key,
                        audio_base64=audio_base64,
                        sample_rate=int(
                            data.get("sample_rate")
                            or data.get("audio_sample_rate")
                            or OUTPUT_SAMPLE_RATE
                        ),
                        chunk_index=_optional_int(data.get("chunk_index")),
                        segment_index=_optional_int(data.get("segment_index")),
                        playback_metadata=playback_metadata,
                    )
                return extras, None

            if event == "delta":
                behavior_results = behavior_tracker.trigger_from_fragment(
                    str(data.get("delta") or "")
                )
                submit_behavior_actions(behavior_results)
                for result in behavior_results:
                    extras.append(("behavior", _behavior_result_payload(result)))
                return extras, None

            if event != "done":
                return extras, None

            key = active_playback_key or current_fallback_playback_key()
            playback_metadata = _playback_metadata_from_payload(data, key)
            behavior_results = behavior_tracker.trigger_from_text(
                str(data.get("reply") or "")
            )
            submit_behavior_actions(behavior_results)
            for result in behavior_results:
                extras.append(("behavior", _behavior_result_payload(result)))

            audio_base64 = data.get("audio_base64")
            if (
                playback_sink.active
                and isinstance(audio_base64, str)
                and audio_base64
            ):
                active_playback_key = playback_sink.enqueue_audio(
                    key,
                    audio_base64=audio_base64,
                    sample_rate=int(
                        data.get("sample_rate")
                        or data.get("audio_sample_rate")
                        or OUTPUT_SAMPLE_RATE
                    ),
                    chunk_index=_optional_int(data.get("chunk_index")),
                    segment_index=_optional_int(data.get("segment_index")),
                    playback_metadata=playback_metadata,
                )
                key = active_playback_key

            if not playback_sink.active:
                reset_playback_group()
                return extras, None

            done_event = threading.Event()
            playback_sink.complete(
                key,
                done_event=done_event,
                playback_metadata=playback_metadata,
            )
            reset_playback_group()
            return extras, done_event

        return hook

    return factory

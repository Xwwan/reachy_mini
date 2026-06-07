"""自动语音流式输出 hook。

AutoVoiceSession 只负责识别、状态机和对话服务调用；本模块把 Interaction
返回的 audio/delta/done 事件转换成机器人播放任务和行为触发事件，让会话层
不用关心具体机器人 IO。
"""

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
    """创建按自动语音 session 隔离的流事件 hook。"""

    playback_sink = (
        RobotPlaybackSink(playback_scheduler)
        if playback_scheduler is not None
        else NullPlaybackSink()
    )

    def factory(session_id: str):
        # 每个自动语音 session 都有独立的播放 key 和行为追踪器，避免多会话
        # 并发时把不同回复的音频、动作或触发标签串在一起。
        active_playback_key: str | None = None
        active_fallback_playback_key: str | None = None
        behavior_tracker = BehaviorTriggerTracker(behavior_config)

        def current_fallback_playback_key() -> str:
            """为没有显式 playback_key 的服务响应生成稳定 fallback key。"""

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
            """把文本触发到的动作信号转成机器人 action job。"""

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
                # 流式 TTS 音频到达时立即排队播放；done 事件再补齐完成屏障。
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
                # 文本增量可提前触发表情/动作，使机器人不必等整句回复结束。
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
            # done 事件用于标记一轮机器人播放结束，同时处理服务端可能随 done
            # 一起返回的整段音频。
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

"""自动语音模式共享类型。

本模块只放轻量级类型和配置对象，避免 manager/session/hooks 之间互相
导入造成循环依赖。自动语音有两种输入来源：
- local：浏览器麦克风把 PCM chunk 通过 HTTP 送到后端。
- robot：后端直接从 Reachy Mini 的媒体接口轮询机器人麦克风。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np

from ..vad import VadConfig

AutoVoiceMode = Literal["local", "robot"]
AutoVoiceGateState = Literal["awake", "waiting_wake"]

# 机器人麦克风读取回调：返回 float 音频数组和采样率；没有新音频时返回 None。
RobotAudioSource = Callable[[], tuple[np.ndarray | None, int]]
# Interaction 流式响应 hook：可追加前端事件，也可返回播放完成屏障供状态机等待。
StreamHook = Callable[[str, dict], tuple[list[tuple[str, dict]], threading.Event | None]]
StreamHookFactory = Callable[[str], StreamHook]


@dataclass(frozen=True)
class WakeGateConfig:
    """唤醒词门控配置。

    enabled=False 时，任意一句用户语音都会进入对话服务；enabled=True 时，
    需要先命中 wake_phrases 才进入 awake 状态，命中 exit_phrases 或空闲超时
    后回到 waiting_wake。
    """

    enabled: bool = False
    wake_phrases: tuple[str, ...] = ()
    exit_phrases: tuple[str, ...] = ()
    idle_timeout_seconds: float = 60.0
    wake_reply: str = "我在。"
    sleep_reply: str = "好，我先休息。"


@dataclass
class AutoVoiceConfig:
    """自动语音运行参数。

    这些参数既可以来自 behavior_config.yaml，也可以由环境变量覆盖；session
    创建后会持有一份配置快照，保证一个会话内的 VAD、队列和播放等待策略稳定。
    """

    vad: VadConfig
    input_gain: float = 1.0
    local_chunk_queue_size: int = 80
    robot_poll_seconds: float = 0.01
    transcript_poll_seconds: float = 0.3
    service_timeout_seconds: int = 120
    playback_wait_grace_seconds: float = 0.1
    playback_wait_max_seconds: float = 0.0
    wake_gate: WakeGateConfig = field(default_factory=WakeGateConfig)


@dataclass
class AutoVoiceSnapshot:
    """暴露给前端的会话快照。

    前端轮询或 SSE snapshot 会使用它来恢复按钮状态、展示 VAD 音量和最近错误。
    """

    session_id: str
    mode: AutoVoiceMode
    state: str
    conversation_id: str
    tts_enabled: bool
    utterance_count: int
    gate_state: AutoVoiceGateState
    wake_gate_enabled: bool
    last_error: str | None
    speech_probability: float
    rms: float
    peak: float

"""FastAPI 请求体模型。

路由层使用这些 Pydantic model 做基础字段校验，复杂规则仍放在各路由的
validate/helper 函数里处理。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..core.constants import INPUT_SAMPLE_RATE


class SettingsPayload(BaseModel):
    service_url: str | None = None
    conversation_id: str | None = None
    tts_sample_rate: int | None = None


class DemoProfilePayload(BaseModel):
    profile: str


class InteractionSessionPayload(BaseModel):
    workflow: str = "chat"
    conversation_id: str | None = None
    input_mode: str = "text"
    tts_enabled: bool = True


class InteractionTextStreamPayload(BaseModel):
    interaction_session_id: str
    workflow: str = "chat"
    message: str
    tts_enabled: bool = True


class InteractionLiveStartPayload(BaseModel):
    interaction_session_id: str
    workflow: str = "chat"
    sample_rate: int = INPUT_SAMPLE_RATE
    channels: int = 1
    audio_format: str = "pcm"


class InteractionLiveChunkPayload(BaseModel):
    interaction_session_id: str
    workflow: str = "chat"
    live_session_id: str
    audio_base64: str
    is_final: bool = False


class InteractionLiveFinishStreamPayload(BaseModel):
    interaction_session_id: str
    workflow: str = "chat"
    live_session_id: str
    tts_enabled: bool = True


class InteractionLiveAbortPayload(BaseModel):
    interaction_session_id: str
    workflow: str = "chat"
    live_session_id: str


class VolumePayload(BaseModel):
    volume: int = Field(..., ge=0, le=100)


class RobotMicInteractionStartPayload(BaseModel):
    interaction_session_id: str
    workflow: str = "chat"


class RobotMicInteractionFinishStreamPayload(BaseModel):
    tts_enabled: bool = True


class AutoVoiceStartPayload(BaseModel):
    input_mode: str
    workflow: str = "chat"
    conversation_id: str | None = None
    tts_enabled: bool = True


class AutoVoiceChunkPayload(BaseModel):
    session_id: str
    audio_base64: str
    sample_rate: int = INPUT_SAMPLE_RATE


class AutoVoiceStopPayload(BaseModel):
    session_id: str


class MemoryCuratePayload(BaseModel):
    conversation_id: str | None = None
    history_limit: int = Field(50, ge=1, le=500)

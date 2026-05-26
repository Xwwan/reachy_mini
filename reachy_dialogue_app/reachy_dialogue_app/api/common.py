from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from ..core.constants import (
    DEFAULT_CONVERSATION_ID,
    DEFAULT_SERVICE_URL,
    OUTPUT_SAMPLE_RATE,
)
from ..interaction import InteractionApiError


def _validate_workflow(value: str) -> str:
    workflow = value.strip()
    if workflow not in {"chat", "onboarding"}:
        raise HTTPException(
            status_code=422,
            detail="workflow must be 'chat' or 'onboarding'.",
        )
    return workflow


def _validate_input_mode(value: str) -> str:
    input_mode = value.strip()
    if input_mode not in {"text", "local", "robot", "auto"}:
        raise HTTPException(
            status_code=422,
            detail="input_mode must be 'text', 'local', 'robot', or 'auto'.",
        )
    return input_mode


def _required_string(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} is required.",
        )
    return stripped


def _interaction_http_exception(exc: InteractionApiError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code or 502,
        detail=exc.message,
    )


def _default_settings() -> dict[str, Any]:
    return {
        "service_url": os.environ.get("REACHY_DIALOGUE_SERVICE_URL", DEFAULT_SERVICE_URL),
        "conversation_id": os.environ.get(
            "REACHY_DIALOGUE_CONVERSATION_ID", DEFAULT_CONVERSATION_ID
        ),
        "tts_sample_rate": int(
            os.environ.get("REACHY_DIALOGUE_TTS_SAMPLE_RATE", OUTPUT_SAMPLE_RATE)
        ),
    }

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from cookAIware.data_store import load_json, save_json


SETTINGS_FILE = "app_settings.json"
DEFAULT_LANGUAGE = "en"


def _settings_path(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE


def get_settings(data_dir: Path) -> Dict[str, Any]:
    settings = load_json(_settings_path(data_dir), {})
    if not isinstance(settings, dict):
        settings = {}
    if "language" not in settings:
        settings["language"] = DEFAULT_LANGUAGE
    return settings


def get_language(data_dir: Path) -> str:
    settings = get_settings(data_dir)
    language = str(settings.get("language", DEFAULT_LANGUAGE)).strip()
    return language or DEFAULT_LANGUAGE


def set_language(data_dir: Path, language: str) -> Dict[str, Any]:
    settings = get_settings(data_dir)
    lang = str(language or "").strip()
    settings["language"] = lang or DEFAULT_LANGUAGE
    save_json(_settings_path(data_dir), settings)
    return settings

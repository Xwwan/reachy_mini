from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from cookAIware.data_store import load_json, save_json


PROFILE_FILE = "family_profile.json"


def _profile_path(data_dir: Path) -> Path:
    return data_dir / PROFILE_FILE


def default_schedule() -> Dict[str, Any]:
    return {
        "weekday": {
            "dinner": True,
            "lunch_adults": 1,
            "lunch_days": ["monday", "wednesday", "friday"],
        },
        "weekend": {
            "breakfast": False,
            "lunch": True,
            "dinner": True,
        },
    }


def load_profile(data_dir: Path) -> Dict[str, Any]:
    profile = load_json(_profile_path(data_dir), {})
    if not profile:
        profile = {
            "adults": None,
            "children": None,
            "schedule": default_schedule(),
        }
    if "schedule" not in profile or not isinstance(profile.get("schedule"), dict):
        profile["schedule"] = default_schedule()
    return profile


def save_profile(data_dir: Path, profile: Dict[str, Any]) -> None:
    save_json(_profile_path(data_dir), profile)


def update_profile(data_dir: Path, adults: int | None, children: int | None, schedule: Dict[str, Any] | None) -> Dict[str, Any]:
    profile = load_profile(data_dir)
    if adults is not None:
        profile["adults"] = int(adults)
    if children is not None:
        profile["children"] = int(children)
    if schedule:
        profile["schedule"] = schedule
    save_profile(data_dir, profile)
    return profile

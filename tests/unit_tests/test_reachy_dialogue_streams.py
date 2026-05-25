import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def test_reachy_dialogue_frontend_streams() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "tests" / "unit_tests" / "dialogue_stream_mock_test.js"
    subprocess.run(["node", str(script)], cwd=repo_root, check=True)


def test_frontend_targets_interaction_routes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    main_js = (
        repo_root
        / "reachy_dialogue_app"
        / "reachy_dialogue_app"
        / "static"
        / "main.js"
    ).read_text(encoding="utf-8")

    assert "/api/interaction/session" in main_js
    assert "/api/interaction/text-stream" in main_js
    assert "/api/interaction/live/start" in main_js
    assert "/api/robot-mic/start-interaction" in main_js
    assert "/api/auto-voice/start" in main_js
    assert "/api/auto-voice/chunk" in main_js
    assert "/api/text-chat-stream" not in main_js
    assert "/api/followups/stream" not in main_js


def test_legacy_dialogue_routes_are_not_registered() -> None:
    from reachy_dialogue_app.reachy_dialogue_app import main as dialogue_main

    client = TestClient(dialogue_main._build_web_only_app())

    assert client.post("/api/text-chat-stream", json={}).status_code == 404
    assert client.post("/api/voice-chat", json={}).status_code == 404
    assert client.post("/api/local-mic/start").status_code == 404
    assert client.get("/api/followups/pending").status_code == 404
    assert client.post("/api/memory/curate", json={}).status_code == 404

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def test_reachy_dialogue_frontend_streams() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "tests" / "unit_tests" / "dialogue_stream_mock_test.js"
    subprocess.run(["node", str(script)], cwd=repo_root, check=True)


def test_text_chat_stream_adds_tts_audio_when_upstream_is_text_only(monkeypatch) -> None:
    from reachy_dialogue_app.reachy_dialogue_app import main as dialogue_main

    class FakeTtsClient:
        sample_rate = 24000

        def synthesize_stream(self, text: str):
            assert text == "你好世界"
            yield b"pcm-a"
            yield b"pcm-b"

    class FakeSseResponse:
        ok = True
        status_code = 200
        text = ""

        def iter_lines(self, chunk_size=1, decode_unicode=True):
            frames = [
                "event: meta",
                'data: {"request_id":"req-text","turn_id":"turn-user","conversation_id":"stream-test"}',
                "",
                "event: delta",
                'data: {"delta":"你好"}',
                "",
                "event: delta",
                'data: {"delta":"世界"}',
                "",
                "event: done",
                'data: {"request_id":"req-text","turn_id":"turn-user","conversation_id":"stream-test","reply":"你好世界"}',
                "",
            ]
            yield from frames

        def close(self):
            return None

    def fake_post(url, **kwargs):
        assert str(url).endswith("/chat/stream")
        assert kwargs["json"]["tts_enabled"] is True
        return FakeSseResponse()

    monkeypatch.setattr(dialogue_main, "_build_text_tts_client", lambda config: FakeTtsClient())
    monkeypatch.setattr(dialogue_main.requests, "post", fake_post)

    app = dialogue_main._build_web_only_app()
    client = TestClient(app)
    response = client.post(
        "/api/text-chat-stream",
        json={
            "text": "测试一下",
            "conversation_id": "stream-test",
            "tts_enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.text
    assert 'event: audio' in body
    assert '"audio_base64": "cGNtLWE="' in body
    assert '"audio_base64": "cGNtLWI="' in body
    assert body.index("event: audio") < body.index("event: done")

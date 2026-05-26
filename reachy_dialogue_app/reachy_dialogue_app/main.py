import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from reachy_mini import ReachyMini, ReachyMiniApp

from .api.auto_voice_routes import _register_auto_voice_routes
from .api.common import _default_settings, _validate_workflow
from .api.followup_memory_routes import _register_followup_memory_routes
from .api.interaction_routes import _register_interaction_routes
from .api.robot_routes import _register_robot_routes
from .api.settings_routes import _register_settings_routes

from .audio.playback import (
    RobotAudioPlaybackScheduler,
    RobotJob,
)
from .audio.robot_mic import RobotMicPlaybackTester, RobotMicRecorder
from .audio.robot_output import (
    _handle_robot_job,
    _report_robot_job_playback_result,
)
from .auto_voice import (
    AutoVoiceManager,
    _auto_voice_config,
    _auto_voice_model_path,
    _auto_voice_stream_hook_factory,
)
from .behavior import (
    _disable_behavior_module,
    _load_behavior_config,
)
from .core.constants import DEFAULT_ROBOT_PORT
from .core.settings import _snapshot
from .interaction import InteractionApiClient


class ReachyDialogueApp(ReachyMiniApp):
    custom_app_url: str | None = "http://0.0.0.0:8042"
    request_media_backend: str | None = None

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        assert self.settings_app is not None

        settings_lock = threading.Lock()
        settings = _default_settings()
        jobs: queue.Queue[RobotJob] = queue.Queue()
        playback_scheduler = RobotAudioPlaybackScheduler(jobs)
        failed_playback_keys: set[str] = set()
        recorder = RobotMicRecorder(reachy_mini)
        playback_tester = RobotMicPlaybackTester(reachy_mini)
        behavior_config = _load_behavior_config()
        auto_voice_manager = AutoVoiceManager(
            model_path=_auto_voice_model_path(behavior_config),
            config=_auto_voice_config(behavior_config),
            service_url_getter=lambda: _snapshot(settings, settings_lock)[
                "service_url"
            ],
            robot_audio_source=lambda: (
                reachy_mini.media.get_audio_sample(),
                reachy_mini.media.get_input_audio_samplerate(),
            ),
            stream_hook_factory=_auto_voice_stream_hook_factory(
                playback_scheduler,
                behavior_config,
            ),
        )

        _register_settings_routes(
            self.settings_app,
            settings,
            settings_lock,
            behavior_config=behavior_config,
        )

        _register_interaction_routes(
            self.settings_app,
            settings,
            settings_lock,
            behavior_config=behavior_config,
            playback_scheduler=playback_scheduler,
        )
        _register_followup_memory_routes(
            self.settings_app,
            settings,
            settings_lock,
            behavior_config=behavior_config,
            playback_scheduler=playback_scheduler,
        )
        _register_auto_voice_routes(
            self.settings_app,
            settings,
            settings_lock,
            auto_voice_manager,
            allow_robot=True,
        )

        _register_robot_routes(
            self.settings_app,
            settings,
            settings_lock,
            reachy_mini=reachy_mini,
            recorder=recorder,
            playback_tester=playback_tester,
            playback_scheduler=playback_scheduler,
            behavior_config=behavior_config,
        )

        while not stop_event.is_set():
            try:
                job = jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            _process_robot_job(
                reachy_mini,
                job,
                service_url=_snapshot(settings, settings_lock)["service_url"],
                failed_playback_keys=failed_playback_keys,
            )


def _process_robot_job(
    reachy_mini: ReachyMini,
    job: RobotJob,
    *,
    service_url: str,
    failed_playback_keys: set[str],
    client_factory: Callable[[str], InteractionApiClient] = InteractionApiClient,
) -> None:
    try:
        result = _handle_robot_job(reachy_mini, job)
        metadata = job.playback_metadata
        playback_key = metadata.playback_key if metadata is not None else None
        if playback_key and not result.ok:
            failed_playback_keys.add(playback_key)

        should_report = bool(
            metadata
            and metadata.playback_key
            and metadata.run_id
            and (job.report_playback_done or not result.ok)
        )
        if not should_report:
            return

        if (
            result.ok
            and job.report_playback_done
            and playback_key in failed_playback_keys
        ):
            return

        try:
            _report_robot_job_playback_result(
                client_factory(service_url),
                job,
                result,
            )
        except Exception as exc:
            print(f"Robot playback status report failed: {exc}")
    finally:
        metadata = job.playback_metadata
        playback_key = metadata.playback_key if metadata is not None else None
        if job.report_playback_done and playback_key:
            failed_playback_keys.discard(playback_key)
        if job.done_event is not None:
            job.done_event.set()


def _build_web_only_app() -> FastAPI:
    app = FastAPI()
    settings_lock = threading.Lock()
    settings = _default_settings()
    behavior_config = _load_behavior_config()
    _disable_behavior_module(behavior_config, "action")
    auto_voice_manager = AutoVoiceManager(
        model_path=_auto_voice_model_path(behavior_config),
        config=_auto_voice_config(behavior_config),
        service_url_getter=lambda: _snapshot(settings, settings_lock)["service_url"],
        stream_hook_factory=_auto_voice_stream_hook_factory(
            None,
            behavior_config,
        ),
    )
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index_page() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    _register_settings_routes(app, settings, settings_lock, behavior_config=behavior_config)
    _register_interaction_routes(
        app,
        settings,
        settings_lock,
        behavior_config=behavior_config,
    )
    _register_followup_memory_routes(
        app,
        settings,
        settings_lock,
        behavior_config=behavior_config,
    )
    _register_auto_voice_routes(
        app,
        settings,
        settings_lock,
        auto_voice_manager,
        allow_robot=False,
    )

    @app.get("/api/app-mode")
    def app_mode() -> dict[str, Any]:
        return {"web_only": True}

    @app.get("/api/audio-volume")
    def web_only_audio_volume() -> dict[str, Any]:
        return {
            "speaker": {"volume": None, "available": False},
            "microphone": {"volume": None, "available": False},
        }


    return app


def run_web_only(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(_build_web_only_app(), host=host, port=port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Reachy voice dialogue app.")
    parser.add_argument(
        "--robot-host",
        default=os.environ.get("REACHY_ROBOT_HOST"),
        help=(
            "Reachy daemon hostname or IP. Use the robot IP for Wireless, "
            "or 127.0.0.1 for a local Lite/sim daemon."
        ),
    )
    parser.add_argument(
        "--robot-port",
        type=int,
        default=int(os.environ.get("REACHY_ROBOT_PORT", DEFAULT_ROBOT_PORT)),
        help="Reachy daemon HTTP/WebSocket port.",
    )
    parser.add_argument(
        "--spawn-daemon",
        action="store_true",
        default=os.environ.get("REACHY_SPAWN_DAEMON", "").lower()
        in {"1", "true", "yes"},
        help="Start reachy-mini-daemon before connecting.",
    )
    parser.add_argument(
        "--use-sim",
        action="store_true",
        default=os.environ.get("REACHY_USE_SIM", "").lower() in {"1", "true", "yes"},
        help="Use the MuJoCo simulated daemon when --spawn-daemon is set.",
    )
    parser.add_argument(
        "--mockup-sim",
        action="store_true",
        default=os.environ.get("REACHY_MOCKUP_SIM", "").lower()
        in {"1", "true", "yes"},
        help="Start a lightweight mockup daemon that does not require MuJoCo.",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        default=os.environ.get("REACHY_DIALOGUE_WEB_ONLY", "").lower()
        in {"1", "true", "yes"},
        help="Serve the browser-only text and local-microphone pages; do not connect to Reachy.",
    )
    parser.add_argument(
        "--web-host",
        default=os.environ.get("REACHY_DIALOGUE_WEB_HOST", "127.0.0.1"),
        help="Host for --web-only mode.",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=int(os.environ.get("REACHY_DIALOGUE_WEB_PORT", "8042")),
        help="Port for --web-only mode.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.web_only:
        run_web_only(args.web_host, args.web_port)
        return

    if args.mockup_sim:
        _spawn_mockup_daemon()
        args.spawn_daemon = False
        args.use_sim = False
        args.robot_host = args.robot_host or "127.0.0.1"

    robot_host = args.robot_host
    if robot_host is None:
        if args.spawn_daemon:
            robot_host = "127.0.0.1"
        else:
            print(
                "Reachy Mini daemon host is required.\n\n"
                "Wireless:\n"
                "  python -m reachy_dialogue_app.main --robot-host <robot-ip>\n\n"
                "Lite / local daemon:\n"
                "  python -m reachy_dialogue_app.main --robot-host 127.0.0.1 --spawn-daemon\n\n"
                "Simulation:\n"
                "  python -m reachy_dialogue_app.main --mockup-sim\n",
                file=sys.stderr,
            )
            raise SystemExit(2)

    app = ReachyDialogueApp()
    try:
        app.wrapped_run(
            host=robot_host,
            port=args.robot_port,
            spawn_daemon=args.spawn_daemon,
            use_sim=args.use_sim,
        )
    except KeyboardInterrupt:
        app.stop()


def _spawn_mockup_daemon() -> None:
    subprocess.Popen(
        [
            "reachy-mini-daemon",
            "--mockup-sim",
            "--no-media",
            "--headless",
            "--localhost-only",
        ],
        start_new_session=True,
    )
    deadline = time.time() + 10.0
    last_error = ""
    while time.time() < deadline:
        try:
            response = requests.get(
                "http://127.0.0.1:8000/api/daemon/status",
                timeout=1,
            )
            if response.ok and response.json().get("state") == "running":
                return
            last_error = response.text
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Mockup daemon did not become ready: {last_error}")


if __name__ == "__main__":
    main()

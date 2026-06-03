"""Bidirectional local audio stream with optional settings UI.

In headless mode, there is no Gradio UI. If the OpenAI API key is not
available via environment/.env, we expose a minimal settings page via the
Reachy Mini Apps settings server to let non-technical users enter it.

The settings UI is served from this package's ``static/`` folder and offers a
single password field to set ``OPENAI_API_KEY``. Once set, we persist it to the
app instance's ``.env`` file (if available) and proceed to start streaming.
"""

import os
import sys
import time
import asyncio
import logging
from typing import List, Optional, Deque, Dict
from collections import deque
from pathlib import Path

from fastrtc import AdditionalOutputs, audio_to_float32
from scipy.signal import resample

from reachy_mini import ReachyMini
from reachy_mini.media.media_manager import MediaBackend
from cookAIware.config import LOCKED_PROFILE, config
from cookAIware.openai_realtime import OpenaiRealtimeHandler
from cookAIware.headless_personality_ui import mount_personality_routes


try:
    # FastAPI is provided by the Reachy Mini Apps runtime
    from fastapi import FastAPI, Response
    from pydantic import BaseModel
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover - only loaded when settings_app is used
    FastAPI = object  # type: ignore
    FileResponse = object  # type: ignore
    JSONResponse = object  # type: ignore
    StaticFiles = object  # type: ignore
    BaseModel = object  # type: ignore


logger = logging.getLogger(__name__)


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    def __init__(
        self,
        handler: OpenaiRealtimeHandler,
        robot: ReachyMini,
        *,
        settings_app: Optional[FastAPI] = None,
        instance_path: Optional[str] = None,
    ):
        """Initialize the stream with an OpenAI realtime handler and pipelines.

        - ``settings_app``: the Reachy Mini Apps FastAPI to attach settings endpoints.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        """
        self.handler = handler
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        # Allow the handler to flush the player queue when appropriate.
        self.handler._clear_queue = self.clear_audio_queue
        self._settings_app: Optional[FastAPI] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._settings_initialized = False
        self._asyncio_loop = None
        self._transcript: Deque[Dict[str, str]] = deque(maxlen=200)

    # ---- Settings UI (only when API key is missing) ----
    def _read_env_lines(self, env_path: Path) -> list[str]:
        """Load env file contents or a template as a list of lines."""
        inst = env_path.parent
        try:
            if env_path.exists():
                try:
                    return env_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return []
            template_text = None
            ex = inst / ".env.example"
            if ex.exists():
                try:
                    template_text = ex.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                try:
                    cwd_example = Path.cwd() / ".env.example"
                    if cwd_example.exists():
                        template_text = cwd_example.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                packaged = Path(__file__).parent / ".env.example"
                if packaged.exists():
                    try:
                        template_text = packaged.read_text(encoding="utf-8")
                    except Exception:
                        template_text = None
            return template_text.splitlines() if template_text else []
        except Exception:
            return []

    def _persist_api_key(self, key: str) -> None:
        """Persist API key to environment and instance ``.env`` if possible.

        Behavior:
        - Always sets ``OPENAI_API_KEY`` in process env and in-memory config.
        - Writes/updates ``<instance_path>/.env``:
          * If ``.env`` exists, replaces/append OPENAI_API_KEY line.
          * Else, copies template from ``<instance_path>/.env.example`` when present,
            otherwise falls back to the packaged template
            ``cookAIware/.env.example``.
          * Ensures the resulting file contains the full template plus the key.
        - Loads the written ``.env`` into the current process environment.
        """
        k = (key or "").strip()
        if not k:
            return
        # Update live process env and config so consumers see it immediately
        try:
            os.environ["OPENAI_API_KEY"] = k
        except Exception:  # best-effort
            pass
        try:
            config.OPENAI_API_KEY = k
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            inst = Path(self._instance_path)
            env_path = inst / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith("OPENAI_API_KEY="):
                    lines[i] = f"OPENAI_API_KEY={k}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"OPENAI_API_KEY={k}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted OPENAI_API_KEY to %s", env_path)

            # Load the newly written .env into this process to ensure downstream imports see it
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist OPENAI_API_KEY: %s", e)

    def _persist_personality(self, profile: Optional[str]) -> None:
        """Persist the startup personality to the instance .env and config."""
        if LOCKED_PROFILE is not None:
            return
        selection = (profile or "").strip() or None
        try:
            from cookAIware.config import set_custom_profile

            set_custom_profile(selection)
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            env_path = Path(self._instance_path) / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(list(lines)):
                if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                    if selection:
                        lines[i] = f"REACHY_MINI_CUSTOM_PROFILE={selection}"
                    else:
                        lines.pop(i)
                    replaced = True
                    break
            if selection and not replaced:
                lines.append(f"REACHY_MINI_CUSTOM_PROFILE={selection}")
            if selection is None and not env_path.exists():
                return
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted startup personality to %s", env_path)
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist REACHY_MINI_CUSTOM_PROFILE: %s", e)

    def _read_persisted_personality(self) -> Optional[str]:
        """Read persisted startup personality from instance .env (if any)."""
        if not self._instance_path:
            return None
        env_path = Path(self._instance_path) / ".env"
        try:
            if env_path.exists():
                for ln in env_path.read_text(encoding="utf-8").splitlines():
                    if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                        _, _, val = ln.partition("=")
                        v = val.strip()
                        return v or None
        except Exception:
            pass
        return None

    def _init_settings_ui_if_needed(self) -> None:
        """Attach minimal settings UI to the settings app.

        Always mounts the UI when a settings_app is provided so that users
        see a confirmation message even if the API key is already configured.
        """
        if self._settings_initialized:
            return
        if self._settings_app is None:
            return

        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        data_dir = None
        try:
            from cookAIware.data_store import resolve_data_dir

            data_dir = resolve_data_dir(self._instance_path)
        except Exception:
            data_dir = None

        if hasattr(self._settings_app, "mount"):
            try:
                # Serve /static/* assets
                self._settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception:
                pass

        class ApiKeyPayload(BaseModel):
            openai_api_key: str

        class TextPayload(BaseModel):
            text: str

        class InventoryPayload(BaseModel):
            items: list[dict]

        class FamilyProfilePayload(BaseModel):
            adults: int | None = None
            children: int | None = None
            schedule: dict | None = None

        class ShoppingListPayload(BaseModel):
            items: list[dict] | None = None

        class SettingsPayload(BaseModel):
            language: str | None = None

        @self._settings_app.get("/transcript")
        def _get_transcript() -> JSONResponse:
            return JSONResponse({"messages": list(self._transcript)})

        # GET / -> index.html
        @self._settings_app.get("/")
        def _root() -> FileResponse:
            return FileResponse(str(index_file))

        # GET /favicon.ico -> optional, avoid noisy 404s on some browsers
        @self._settings_app.get("/favicon.ico")
        def _favicon() -> Response:
            return Response(status_code=204)

        # GET /status -> whether key is set
        @self._settings_app.get("/status")
        def _status() -> JSONResponse:
            has_key = bool(config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip())
            return JSONResponse({"has_key": has_key})

        @self._settings_app.get("/settings")
        def _get_settings() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.app_settings import get_settings

            return JSONResponse({"settings": get_settings(data_dir)})

        @self._settings_app.post("/settings")
        async def _set_settings(payload: SettingsPayload) -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.app_settings import set_language

            language = (payload.language or "").strip()
            if not language:
                return JSONResponse({"error": "missing_language"}, status_code=400)
            settings = set_language(data_dir, language)
            try:
                await self.handler.apply_language(language)
            except Exception:
                pass
            return JSONResponse({"settings": settings})

        @self._settings_app.get("/data/inventory")
        def _get_inventory() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.inventory import list_inventory, normalize_for_display

            items = normalize_for_display(list_inventory(data_dir))
            return JSONResponse({"items": items})

        @self._settings_app.post("/data/inventory/replace")
        def _replace_inventory(payload: InventoryPayload) -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.inventory import save_inventory, normalize_items_for_save

            normalized = normalize_items_for_save(payload.items)
            save_inventory(data_dir, normalized)
            return JSONResponse({"ok": True})

        @self._settings_app.get("/data/family_profile")
        def _get_family_profile() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.family_profile import load_profile

            profile = load_profile(data_dir)
            return JSONResponse({"profile": profile})

        @self._settings_app.post("/data/family_profile")
        def _update_family_profile(payload: FamilyProfilePayload) -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.family_profile import update_profile

            profile = update_profile(data_dir, payload.adults, payload.children, payload.schedule)
            return JSONResponse({"profile": profile})

        @self._settings_app.get("/data/meal_plan")
        def _get_meal_plan() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.meal_planner import load_plan

            plan = load_plan(data_dir)
            return JSONResponse({"plan": plan})

        @self._settings_app.post("/data/meal_plan/generate")
        def _generate_meal_plan() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.inventory import list_inventory
            from cookAIware.family_profile import load_profile
            from cookAIware.meal_planner import generate_plan

            profile = load_profile(data_dir)
            adults = profile.get("adults")
            children = profile.get("children")
            if adults is None or children is None:
                return JSONResponse({"error": "missing_family_profile"}, status_code=400)
            inventory = list_inventory(data_dir)
            plan = generate_plan(data_dir, inventory, int(adults), int(children))
            return JSONResponse({"plan": plan})

        @self._settings_app.get("/data/shopping_list")
        def _get_shopping_list() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.shopping_list import list_items

            items = list_items(data_dir)
            return JSONResponse({"items": items})

        @self._settings_app.post("/data/shopping_list/generate")
        def _generate_shopping_list() -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.meal_planner import load_plan
            from cookAIware.inventory import list_inventory
            from cookAIware.shopping_list import generate_from_plan

            plan = load_plan(data_dir)
            inventory = list_inventory(data_dir)
            items = generate_from_plan(data_dir, plan, inventory)
            return JSONResponse({"items": items})

        @self._settings_app.post("/data/shopping_list/replace")
        def _replace_shopping_list(payload: ShoppingListPayload) -> JSONResponse:
            if data_dir is None:
                return JSONResponse({"error": "data_dir_unavailable"}, status_code=500)
            from cookAIware.shopping_list import save_list

            save_list(data_dir, payload.items or [])
            return JSONResponse({"ok": True})

        # GET /ready -> whether backend finished loading tools
        @self._settings_app.get("/ready")
        def _ready() -> JSONResponse:
            try:
                mod = sys.modules.get("cookAIware.tools.core_tools")
                ready = bool(getattr(mod, "_TOOLS_INITIALIZED", False)) if mod else False
            except Exception:
                ready = False
            return JSONResponse({"ready": ready})

        # POST /openai_api_key -> set/persist key
        @self._settings_app.post("/openai_api_key")
        def _set_key(payload: ApiKeyPayload) -> JSONResponse:
            key = (payload.openai_api_key or "").strip()
            if not key:
                return JSONResponse({"ok": False, "error": "empty_key"}, status_code=400)
            self._persist_api_key(key)
            return JSONResponse({"ok": True})

        # POST /validate_api_key -> validate key without persisting it
        @self._settings_app.post("/validate_api_key")
        async def _validate_key(payload: ApiKeyPayload) -> JSONResponse:
            key = (payload.openai_api_key or "").strip()
            if not key:
                return JSONResponse({"valid": False, "error": "empty_key"}, status_code=400)

            # Try to validate by checking if we can fetch the models
            try:
                import httpx

                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get("https://api.openai.com/v1/models", headers=headers)
                    if response.status_code == 200:
                        return JSONResponse({"valid": True})
                    elif response.status_code == 401:
                        return JSONResponse({"valid": False, "error": "invalid_api_key"}, status_code=401)
                    else:
                        return JSONResponse(
                            {"valid": False, "error": "validation_failed"}, status_code=response.status_code
                        )
            except Exception as e:
                logger.warning(f"API key validation failed: {e}")
                return JSONResponse({"valid": False, "error": "validation_error"}, status_code=500)

        @self._settings_app.post("/text_input")
        async def _text_input(payload: TextPayload) -> JSONResponse:
            text = (payload.text or "").strip()
            if not text:
                return JSONResponse({"ok": False, "error": "empty_text"}, status_code=400)
            if not self._asyncio_loop:
                return JSONResponse({"ok": False, "error": "not_ready"}, status_code=400)
            try:
                self._transcript.append({"role": "user", "content": text})
                future = asyncio.run_coroutine_threadsafe(
                    self.handler.send_text(text),
                    self._asyncio_loop,
                )
                ok = future.result(timeout=5)
                return JSONResponse({"ok": bool(ok)})
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        self._settings_initialized = True

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        If the OpenAI key is missing, expose a tiny settings UI via the
        Reachy Mini settings server to collect it before starting streams.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first (covers subsequent runs)
        if self._instance_path:
            try:
                from dotenv import load_dotenv

                from cookAIware.config import set_custom_profile

                env_path = Path(self._instance_path) / ".env"
                if env_path.exists():
                    load_dotenv(dotenv_path=str(env_path), override=True)
                    # Update config with newly loaded values
                    new_key = os.getenv("OPENAI_API_KEY", "").strip()
                    if new_key:
                        try:
                            config.OPENAI_API_KEY = new_key
                        except Exception:
                            pass
                    if LOCKED_PROFILE is None:
                        new_profile = os.getenv("REACHY_MINI_CUSTOM_PROFILE")
                        if new_profile is not None:
                            try:
                                set_custom_profile(new_profile.strip() or None)
                            except Exception:
                                pass  # Best-effort profile update
            except Exception:
                pass  # Instance .env loading is optional; continue with defaults

        # If key is still missing, try to download one from HuggingFace
        if not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
            logger.info("OPENAI_API_KEY not set, attempting to download from HuggingFace...")
            try:
                from gradio_client import Client

                client = Client("HuggingFaceM4/gradium_setup", verbose=False)
                key, status = client.predict(api_name="/claim_b_key")
                if key and key.strip():
                    logger.info("Successfully downloaded API key from HuggingFace")
                    # Persist it immediately
                    self._persist_api_key(key)
            except Exception as e:
                logger.warning(f"Failed to download API key from HuggingFace: {e}")

        # Always expose settings UI if a settings app is available
        # (do this AFTER loading/downloading the key so status endpoint sees the right value)
        self._init_settings_ui_if_needed()

        # If key is still missing -> wait until provided via the settings UI
        if not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
            logger.warning("OPENAI_API_KEY not found. Open the app settings page to enter it.")
            # Poll until the key becomes available (set via the settings UI)
            try:
                while not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Interrupted while waiting for API key.")
                return

        # Configure audio devices before starting media (best-effort)
        self._configure_sounddevice()

        # Start media after key is set/available
        self._robot.media.start_recording()
        self._robot.media.start_playing()
        time.sleep(1)  # give some time to the pipelines to start

        async def runner() -> None:
            # Capture loop for cross-thread personality actions
            loop = asyncio.get_running_loop()
            self._asyncio_loop = loop  # type: ignore[assignment]
            # Mount personality routes now that loop and handler are available
            try:
                if self._settings_app is not None:
                    mount_personality_routes(
                        self._settings_app,
                        self.handler,
                        lambda: self._asyncio_loop,
                        persist_personality=self._persist_personality,
                        get_persisted_personality=self._read_persisted_personality,
                    )
            except Exception:
                pass
            self._tasks = [
                asyncio.create_task(self.handler.start_up(), name="openai-handler"),
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                # Ensure handler connection is closed
                await self.handler.shutdown()

        asyncio.run(runner())

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (openai-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug(f"Error stopping recording (may already be stopped): {e}")

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug(f"Error stopping playback (may already be stopped): {e}")

        # Now signal async loops to stop
        self._stop_event.set()

        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

    def clear_audio_queue(self) -> None:
        """Flush the player's appsrc to drop any queued audio immediately."""
        logger.info("User intervention: flushing player queue")
        if self._robot.media.backend == MediaBackend.GSTREAMER:
            # Directly flush gstreamer audio pipe
            self._robot.media.audio.clear_player()
        elif (
            self._robot.media.backend == MediaBackend.DEFAULT
            or self._robot.media.backend == MediaBackend.DEFAULT_NO_VIDEO
        ):
            self._robot.media.audio.clear_output_buffer()
        self.handler.output_queue = asyncio.Queue()

    def _configure_sounddevice(self) -> None:
        """Best-effort selection of Reachy Mini audio devices on Windows."""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            if not devices:
                return

            def _score(name: str) -> int:
                n = name.lower()
                if "wasapi" in n:
                    return 4
                if "wdm-ks" in n:
                    return 3
                if "directsound" in n:
                    return 2
                if "mme" in n:
                    return 1
                return 0

            def _find_device(targets: List[str], io_type: str) -> Optional[int]:
                candidates = []
                for idx, dev in enumerate(devices):
                    name = str(dev.get("name", ""))
                    if dev.get(f"max_{io_type}_channels", 0) <= 0:
                        continue
                    for t in targets:
                        if t in name.lower():
                            candidates.append((idx, _score(name)))
                            break
                if not candidates:
                    return None
                candidates.sort(key=lambda item: item[1], reverse=True)
                return candidates[0][0]

            def _from_env(key: str, io_type: str) -> Optional[int]:
                val = os.getenv(key, "").strip()
                if not val:
                    return None
                if val.isdigit():
                    return int(val)
                return _find_device([val.lower()], io_type)

            input_id = _from_env("COOKAIWARE_AUDIO_INPUT", "input")
            output_id = _from_env("COOKAIWARE_AUDIO_OUTPUT", "output")

            if input_id is None:
                input_id = _find_device(["reachy mini audio"], "input")
            if input_id is None:
                input_id = _find_device(["reachy mini camera", "reachymini"], "input")
            if input_id is None:
                input_id = _find_device(["usb audio", "respeaker"], "input")
            if output_id is None:
                output_id = _find_device(["reachy mini audio"], "output")
            if output_id is None:
                output_id = _find_device(["usb audio", "respeaker"], "output")

            if input_id is not None or output_id is not None:
                current = list(sd.default.device)
                if input_id is not None:
                    current[0] = input_id
                if output_id is not None:
                    current[1] = output_id
                sd.default.device = tuple(current)
                input_name = devices[current[0]]["name"] if current[0] is not None else "unknown"
                output_name = devices[current[1]]["name"] if current[1] is not None else "unknown"
                logger.info(
                    "SoundDevice default set to input=%s (%s) output=%s (%s)",
                    current[0],
                    input_name,
                    current[1],
                    output_name,
                )
        except Exception as e:
            logger.warning("Failed to configure SoundDevice defaults: %s", e)

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        input_sample_rate = self._robot.media.get_input_audio_samplerate()
        logger.debug(f"Audio recording started at {input_sample_rate} Hz")

        while not self._stop_event.is_set():
            audio_frame = self._robot.media.get_audio_sample()
            if audio_frame is not None:
                await self.handler.receive((input_sample_rate, audio_frame))
            await asyncio.sleep(0)  # avoid busy loop

    async def play_loop(self) -> None:
        """Fetch outputs from the handler: log text and play audio frames."""
        while not self._stop_event.is_set():
            handler_output = await self.handler.emit()

            if isinstance(handler_output, AdditionalOutputs):
                for msg in handler_output.args:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        role = str(msg.get("role", "assistant"))
                        self._transcript.append({"role": role, "content": content})
                        logger.info(
                            "role=%s content=%s",
                            msg.get("role"),
                            content if len(content) < 500 else content[:500] + "…",
                        )

            elif isinstance(handler_output, tuple):
                input_sample_rate, audio_data = handler_output
                output_sample_rate = self._robot.media.get_output_audio_samplerate()

                # Reshape if needed
                if audio_data.ndim == 2:
                    # Scipy channels last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast if needed
                audio_frame = audio_to_float32(audio_data)

                # Resample if needed
                if input_sample_rate != output_sample_rate:
                    audio_frame = resample(
                        audio_frame,
                        int(len(audio_frame) * output_sample_rate / input_sample_rate),
                    )

                self._robot.media.push_audio_sample(audio_frame)

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop

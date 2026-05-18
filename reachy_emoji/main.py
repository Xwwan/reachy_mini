import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import mpv
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

EMOTIONS = ['不屑', '愤怒', '惊恐', '难过', '兴奋', '静态']
DEFAULT_EMOTION = '静态'
CONFIG_ENV = 'REACHY_EMOJI_CONFIG'
DEBUG_ENV = 'REACHY_EMOJI_DEBUG'
DEBUG_LOG_ENV = 'REACHY_EMOJI_DEBUG_LOG'
VIDEO_ROOT = Path(__file__).resolve().parent / 'videos'
DEFAULT_CONFIG = Path(__file__).resolve().parent / 'config.json'

current_emotion = DEFAULT_EMOTION
_pending_emotion = None
_pending_event = threading.Event()
_stop_event = threading.Event()
_state_lock = threading.Lock()


def _log():
    return logging.getLogger('reachy_emoji')


@dataclass(frozen=True)
class EmotionFiles:
    entry: Path
    loop: Path
    exit: Path


class SignalRequest(BaseModel):
    signal: str


class EmotionRequest(BaseModel):
    emotion: str


def _load_config():
    config_path = Path(os.environ.get(CONFIG_ENV, DEFAULT_CONFIG)).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f'Config not found: {config_path}')
    with config_path.open('r', encoding='utf-8') as handle:
        config = json.load(handle)
    _log().debug('Loaded config from %s', config_path)
    return config_path, config


def _pick_static_variant_dir(config):
    base_dir = VIDEO_ROOT / '静态'
    preferred = config.get('static_variant')
    if preferred:
        preferred_dir = base_dir / preferred
        if preferred_dir.is_dir():
            return preferred_dir, preferred

    subdirs = sorted([p for p in base_dir.iterdir() if p.is_dir()])
    if subdirs:
        return subdirs[0], subdirs[0].name

    return base_dir, '静态'


def _resolve_emotion_files(emotion, config):
    if emotion not in EMOTIONS:
        raise ValueError(f'Unknown emotion: {emotion}')

    if emotion == '静态':
        emotion_dir, prefix = _pick_static_variant_dir(config)
    else:
        emotion_dir = VIDEO_ROOT / emotion
        prefix = emotion

    entry = emotion_dir / f'{prefix}_1进入姿势.mp4'
    loop = emotion_dir / f'{prefix}_2可循环动作.mp4'
    exit = emotion_dir / f'{prefix}_3回正.mp4'

    missing = [path for path in (entry, loop, exit) if not path.exists()]
    if missing:
        missing_names = ', '.join(str(path) for path in missing)
        raise FileNotFoundError(f'Missing emotion video(s): {missing_names}')

    return EmotionFiles(entry=entry, loop=loop, exit=exit)


def _load_emotions(config):
    return {emotion: _resolve_emotion_files(emotion, config) for emotion in EMOTIONS}


def _create_player():
    return mpv.MPV(
        vo='tct',
        audio='no',
        osd_level=0,
        term_osd='no',
        keep_open='yes',
    )


def _play_once(player, path):
    _log().debug('Play once: %s', path)
    player.loop_file = 'no'
    player.command('loadfile', str(path), 'replace')
    player.pause = False
    started = False
    duration = None
    start_time = time.monotonic()
    while not _stop_event.is_set():
        time_pos = getattr(player, 'time_pos', None)
        if time_pos is not None:
            started = True
        if duration is None:
            duration = getattr(player, 'duration', None)
        if duration is not None and time_pos is not None and time_pos >= max(duration - 0.05, 0):
            return
        if started and getattr(player, 'core_idle', False):
            _log().debug('Play once finished by idle: %s', path)
            return
        if time.monotonic() - start_time > 30:
            _log().warning('Play once timeout: %s', path)
            return
        _stop_event.wait(timeout=0.05)


def _play_loop_until_change(player, path):
    _log().debug('Play loop: %s', path)
    player.loop_file = 'inf'
    player.command('loadfile', str(path), 'replace')
    while not _stop_event.is_set():
        if _pending_event.wait(timeout=0.1):
            _log().debug('Loop interrupted for switch')
            return
        if getattr(player, 'core_idle', False):
            player.command('loadfile', str(path), 'replace')
        _stop_event.wait(timeout=0.05)


def change_emotion(emotion):
    if emotion not in EMOTIONS:
        raise ValueError(f'Unknown emotion: {emotion}')

    global _pending_emotion
    with _state_lock:
        if emotion == current_emotion:
            _log().debug('Ignored emotion (same as current): %s', emotion)
            return
        _pending_emotion = emotion
        _pending_event.set()
        _log().debug('Queued emotion: %s', emotion)


def _consume_pending():
    global _pending_emotion
    with _state_lock:
        emotion = _pending_emotion
        _pending_emotion = None
        _pending_event.clear()
        if emotion:
            _log().debug('Consumed pending emotion: %s', emotion)
        return emotion


def _set_current_emotion(emotion):
    global current_emotion
    with _state_lock:
        current_emotion = emotion
        _log().debug('Current emotion set: %s', emotion)


def _resolve_signal_to_emotion(signal_value):
    _, config = _load_config()
    mapping = config.get('signal_map', {})
    emotion = mapping.get(signal_value)
    if not emotion:
        raise HTTPException(status_code=404, detail='Unknown signal')
    if emotion not in EMOTIONS:
        raise HTTPException(status_code=400, detail='Unknown emotion')
    _log().debug('Signal resolved: %s -> %s', signal_value, emotion)
    return emotion


def _create_app():
    app = FastAPI()

    @app.post('/signal')
    def handle_signal(payload: SignalRequest):
        emotion = _resolve_signal_to_emotion(payload.signal)
        change_emotion(emotion)
        return {'status': 'ok', 'emotion': emotion}

    @app.get('/{signal_value}')
    def handle_signal_path(signal_value: str):
        emotion = _resolve_signal_to_emotion(signal_value)
        change_emotion(emotion)
        return {'status': 'ok', 'emotion': emotion}

    @app.post('/emotion')
    def handle_emotion(payload: EmotionRequest):
        if payload.emotion not in EMOTIONS:
            raise HTTPException(status_code=400, detail='Unknown emotion')
        change_emotion(payload.emotion)
        return {'status': 'ok', 'emotion': payload.emotion}

    @app.post('/reload')
    def handle_reload():
        _load_config()
        return {'status': 'ok'}

    return app


def _start_server_thread(host, port):
    app = _create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level='info', access_log=False)
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name='reachy-emoji-api', daemon=True)
    thread.start()
    return server, thread


def main():
    handlers = None
    if os.environ.get(DEBUG_ENV):
        log_path = os.environ.get(DEBUG_LOG_ENV)
        if log_path:
            handlers = [logging.FileHandler(log_path, encoding='utf-8')]
        logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s', handlers=handlers)
    else:
        logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

    _, config = _load_config()
    server_cfg = config.get('server', {})
    host = server_cfg.get('host', '0.0.0.0')
    port = int(server_cfg.get('port', 8001))
    _start_server_thread(host, port)

    emotion_files = _load_emotions(config)
    player = _create_player()

    try:
        start_emotion = config.get('default_emotion', DEFAULT_EMOTION)
        if start_emotion not in EMOTIONS:
            raise ValueError(f'Unknown emotion: {start_emotion}')
        _set_current_emotion(start_emotion)
        while not _stop_event.is_set():
            files = emotion_files[current_emotion]
            _play_once(player, files.entry)
            _log().debug('After entry: pending=%s current=%s', _pending_emotion, current_emotion)
            if _pending_event.is_set():
                _log().debug('Skip loop due to pending switch')
                _play_once(player, files.exit)
                next_emotion = _consume_pending()
                if next_emotion and next_emotion != current_emotion:
                    _set_current_emotion(next_emotion)
                continue
            _play_loop_until_change(player, files.loop)

            if _stop_event.is_set():
                break

            next_emotion = _consume_pending()
            if not next_emotion or next_emotion == current_emotion:
                continue

            _play_once(player, files.exit)
            _set_current_emotion(next_emotion)
    finally:
        player.terminate()


if __name__ == '__main__':
    main()




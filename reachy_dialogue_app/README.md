---
title: Reachy Dialogue App
emoji: 👋
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Interaction API voice dialogue bridge for Reachy Mini.
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Reachy Dialogue App

`reachy_dialogue_app` connects Reachy Mini to the new unified backend
Interaction API documented in:

```text
/Users/xwan/code/test-project/docs/api_contracts.md
```

The app is now a local Reachy Mini device bridge plus a browser workbench. It no
longer falls back to the old `/chat`, `/chat/stream`, `/voice/chat`, or
`/voice/live/*` backend routes. Follow-up and memory remain separate backend
capabilities and are exposed only as auxiliary debugging/control routes.

## Architecture

The backend owns dialogue state and exposes the canonical Interaction API:

- `POST /interaction/sessions`
- `GET /interaction/sessions/{interaction_session_id}`
- `GET /interaction/sessions/{interaction_session_id}/runs`
- `GET /interaction/runs/{run_id}`
- `POST /interaction/runs/text-stream`
- `POST /interaction/live/start`
- `POST /interaction/live/chunk`
- `GET /interaction/live/transcript`
- `POST /interaction/live/finish-transcript`
- `POST /interaction/live/finish-stream`
- `POST /interaction/live/abort`
- `POST /interaction/playback/done`
- `POST /interaction/playback/error`

The local app provides a smaller `/api/*` surface for the browser and robot:

- `POST /api/interaction/session`
- `GET /api/interaction/session/{interaction_session_id}`
- `GET /api/interaction/session/{interaction_session_id}/runs`
- `GET /api/interaction/runs/{run_id}`
- `POST /api/interaction/text-stream`
- `POST /api/interaction/live/start`
- `POST /api/interaction/live/chunk`
- `GET /api/interaction/live/transcript`
- `POST /api/interaction/live/finish-stream`
- `POST /api/interaction/live/abort`
- `POST /api/robot-mic/start-interaction`
- `POST /api/robot-mic/finish-interaction-stream`
- `POST /api/auto-voice/start`
- `POST /api/auto-voice/chunk`
- `GET /api/auto-voice/events`
- `GET /api/auto-voice/state`
- `POST /api/auto-voice/stop`
- `GET /api/followups/pending`
- `GET /api/followups/stream`
- `POST /api/followups/{request_id}/run`
- `POST /api/memory/curate`
- `POST /api/memory/profile/refresh`
- settings, health, audio volume, robot mic level/debug, and robot mic playback test helpers

The old local dialogue entrypoints have been removed:

- `/api/text-chat-stream`
- `/api/voice-chat`
- `/api/local-mic/*`
- old `/api/robot-mic/start`, `/api/robot-mic/stop`, `/api/robot-mic/stop-stream`

## Workflows

Every Interaction session has a `workflow`:

- `chat`: normal conversation and backend memory behavior.
- `onboarding`: onboarding state machine. The local app only proxies state and does
  not invent onboarding logic locally.

The browser workbench can switch workflows before creating or using a session.
Both text and voice inputs reuse the same Interaction session until the user
creates a new one.

## Text Interaction

Text input follows this path:

1. Browser calls `POST /api/interaction/session` if no session exists.
2. Browser calls `POST /api/interaction/text-stream`.
3. Local app proxies backend SSE events from `/interaction/runs/text-stream`.
4. `audio` events are queued for robot speaker playback when running on a robot.
5. After real playback finishes, the local app reports:
   - `POST /interaction/playback/done`, or
   - `POST /interaction/playback/error`

The playback queue always prefers backend `playback_key`. `run_id`,
`interaction_session_id`, and `workflow` are carried with playback metadata so
the backend run can track final playback status.

## Voice Interaction

There are three voice modes in the current workbench:

- Local microphone: browser captures audio and sends chunks through
  `/api/interaction/live/*`.
- Robot microphone: the local Python app captures Reachy Mini microphone audio
  and sends chunks through `/interaction/live/*`.
- Auto voice: local VAD detects utterances and uses the same Interaction live
  APIs for each utterance.

Normal "finish and answer" voice turns use:

```text
POST /interaction/live/finish-stream
```

Wake-gated auto voice uses:

```text
POST /interaction/live/finish-transcript
```

That endpoint ends ASR and returns final text only. It does not create an
Interaction run, does not reply, does not advance onboarding, and does not
generate TTS. This keeps wake phrases and exit phrases out of chat/onboarding
history. Once the gate is awake, the real user utterance is sent as text through
`/interaction/runs/text-stream`.

## Behavior Tags

The app still parses behavior tags from model replies and forwards them to local
behavior modules:

- `[emo:angry]`
- `[act:开心]`

Configuration lives in:

```text
reachy_dialogue_app/reachy_dialogue_app/behavior_config.yaml
```

Emoji behavior can call a local HTTP service. Action behavior uses in-process
Reachy Mini control and shares the app's robot connection.

## Start Backend

Start your new backend separately. Example:

```bash
cd /Users/xwan/code/test-project
conda run -n toy python -m src.main --host 127.0.0.1 --port 12312 --log-level DEBUG
```

The default service URL is:

```text
http://127.0.0.1:12312
```

You can also change it in the browser workbench or by setting:

```bash
export REACHY_DIALOGUE_SERVICE_URL=http://127.0.0.1:12312
```

## Start Reachy Dialogue App

From this repository:

```bash
cd /Users/xwan/code/reachy_mini
conda run -n toy python -m reachy_dialogue_app.reachy_dialogue_app.main
```

For Lite with a local daemon:

```bash
conda run -n toy python -m reachy_dialogue_app.reachy_dialogue_app.main \
  --robot-host 127.0.0.1 \
  --spawn-daemon
```

For Wireless, use the robot hostname or IP:

```bash
conda run -n toy python -m reachy_dialogue_app.reachy_dialogue_app.main \
  --robot-host reachy-mini.local
```

For UI and backend testing without a robot:

```bash
REACHY_DIALOGUE_SERVICE_URL=http://127.0.0.1:12312 \
conda run -n toy python -m reachy_dialogue_app.reachy_dialogue_app.main --web-only
```

Open:

```text
http://127.0.0.1:8042/
```

## Browser Workbench

The root page is the main workbench. It includes:

- service URL and conversation ID settings
- `chat` / `onboarding` workflow selector
- Interaction session creation
- streaming text input
- local microphone live interaction
- robot microphone live interaction
- auto voice controls
- current session/run/playback/live identifiers
- session/run debug refresh, including playback status and run errors
- onboarding stage, collected fields, and missing required slots
- follow-up stream is subscribed by default with TTS enabled
- follow-up and memory controls
- raw SSE event log

`web-only` mode hides robot-only controls and keeps local text, local mic, and
auto local voice available.

## Audio Probe

`scripts/dialogue_stream_probe.py` measures the real local app SSE path and TTS
audio chunk timing. It now uses the new Interaction routes:

- `POST /api/interaction/session`
- `POST /api/interaction/text-stream`

Example:

```bash
conda run -n toy python reachy_dialogue_app/scripts/dialogue_stream_probe.py \
  --label mac \
  --app-url http://127.0.0.1:8042 \
  --output /tmp/reachy_dialogue_stream_mac.json \
  --save-audio /tmp/reachy_dialogue_stream_mac.wav
```

Compare two probe outputs:

```bash
conda run -n toy python reachy_dialogue_app/scripts/dialogue_stream_probe.py \
  --compare /tmp/reachy_dialogue_stream_mac.json /tmp/reachy_dialogue_stream_pi.json
```

Useful fields:

- `first_audio_ms`
- `audio_interarrival_ms`
- `audio_chunk_duration_ms`
- `starvation_ms_total`
- `final_backlog_ms`

## Tests

Run the Reachy Dialogue test set:

```bash
conda run -n toy python -m pytest tests/unit_tests/test_reachy_dialogue*.py
```

Run the browser mock directly:

```bash
node tests/unit_tests/dialogue_stream_mock_test.js
```

The current tests cover:

- Interaction client request shapes
- text stream proxying
- live voice start/chunk/transcript/finish/abort
- `finish-transcript` for auto voice wake gate
- playback metadata and playback done/error reporting
- session/run debug route proxying
- follow-up and memory auxiliary route proxying
- new frontend route usage
- removal of old local dialogue routes

## Environment Variables

```bash
export REACHY_DIALOGUE_SERVICE_URL=http://127.0.0.1:12312
export REACHY_DIALOGUE_CONVERSATION_ID=reachy-mini-voice
export REACHY_DIALOGUE_TTS_SAMPLE_RATE=24000
export REACHY_ROBOT_HOST=127.0.0.1
export REACHY_ROBOT_PORT=8000
export REACHY_SPAWN_DAEMON=true
export REACHY_USE_SIM=false
export REACHY_DIALOGUE_WEB_ONLY=false
export REACHY_DIALOGUE_WEB_HOST=127.0.0.1
export REACHY_DIALOGUE_WEB_PORT=8042
export REACHY_DIALOGUE_BEHAVIOR_CONFIG=/path/to/behavior_config.yaml
export REACHY_DIALOGUE_BEHAVIOR_ENABLED=true
export REACHY_DIALOGUE_EMOJI_ENABLED=true
export REACHY_DIALOGUE_EMOJI_SERVICE_URL=http://127.0.0.1:8001
```

## Notes

- The app intentionally does not call legacy backend routes.
- The deleted `local-mic-test.html` flow has been replaced by the main
  workbench's local microphone Interaction flow.
- Follow-up remains separate from Interaction. Late follow-up replies are appended
  to the dialogue tail, and follow-up TTS is grouped by `request_id +
  followup_turn_id` before entering the robot playback queue.
- Memory curate/profile refresh are debug operations; they do not replace the
  Interaction chat/onboarding workflow.

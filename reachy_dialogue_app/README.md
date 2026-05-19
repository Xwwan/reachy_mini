---
title: Reachy Dialogue App
emoji: 👋
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Voice dialogue bridge for a local long-term-memory chat service.
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Reachy Dialogue App

这个 app 把 Reachy Mini 连接到本机已有的对话系统 `/home/tzhx/test-project`。

默认工作方式：

- 对话服务由用户手动启动。
- 默认服务地址是 `http://127.0.0.1:12312`，也可以在 app 页面里修改。
- App 使用机器人麦克风录音，转成 16kHz、16-bit、mono PCM 后通过对话服务的 `/voice/live/*` 实时语音接口分块发送。
- 对话服务负责实时 STT、生成回复、长期记忆检索和 TTS；正式机器人麦克风停止录音时优先请求 `/voice/live/finish-stream`，通过 SSE 接收 `transcript` / `delta` / `audio` / `done` 事件，不再把整段音频发到 `/voice/chat` 做二次识别，也不走延迟测试接口。
- 流式 TTS 会以多个 `audio` 事件返回 24kHz、16-bit、mono PCM chunk；app 会收集这些 chunk，合并后写成临时 WAV 并通过 `reachy_mini.media.play_sound()` 让机器人播放。
- 如果服务端没有正式流式 finish 接口，app 会回退到 `/voice/live/finish` 的非流式 JSON 响应，包装成兼容的 SSE `transcript` / `delta` / `done` 事件，并继续播放返回的 TTS PCM；这个 fallback 仍走记忆检索链路。
- 页面支持手动输入文本。文本会优先转发到对话服务的 `/chat/stream` 并透传 `meta` / `delta` / `done` SSE；如果服务端没有流式文本接口，会回退到 `/chat`，再把完整回复包装成兼容的 `delta` / `done`。返回里如果包含 `audio_base64`、`response_audio_base64` 或 `tts_audio_base64`，会继续由机器人扬声器播放。
- 页面支持调整扬声器和麦克风音量。滑杆通过 Reachy daemon 的 `/api/volume/current`、`/api/volume/set`、`/api/volume/microphone/current`、`/api/volume/microphone/set` 代理读写，取值范围是 0-100。
- Dialogue app 不直接控制机器人动作；它只从模型回复中解析行为标签并向表情/动作模块发送控制信号。
- 页面里临时加入了“机器人麦克风回放测试”：录一段机器人麦克风输入，停止后不经过对话服务，直接从机器人扬声器播放原始录音，方便检查机器人麦克风和扬声器链路。
- App 维护自己的 `reachy_dialogue_app/reachy_dialogue_app/behavior_config.yaml`，用于声明行为模块、可识别的 tag 名和触发 key；当模型回复里出现类似 `[emo:angry]`、`[act:开心]` 的标签时，会把 key 原样转发给对应模块。前端会保留原始标签显示。

## 启动顺序

先启动你的对话系统：

```bash
cd /home/tzhx/test-project
/home/tzhx/miniconda3/bin/conda run -n test python -m src.main --host 127.0.0.1 --port 12312 --log-level DEBUG
```

如果要联动终端表情，另开一个终端启动 Reachy Emoji 服务：

```bash
cd /home/tzhx/wyl/reachy_mini/reachy_emoji
/home/tzhx/miniconda3/bin/conda run -n test python main.py
```

默认表情服务地址是 `http://127.0.0.1:8001`。例如模型回复中包含 `[emo:angry]` 时，
dialogue app 会发出：

```text
GET http://127.0.0.1:8001/angry
```

动作当前使用本进程函数调用。模型回复中包含 `[act:开心]` 时，dialogue app 会把
`开心` 作为 signal 传给 `action_call/play_emotion_action.py` 的可复用函数；具体映射由
`action_call/config.json` 维护。

动作会复用 dialogue app 当前的 ReachyMini 连接，不需要额外启动 `8002` 动作服务。

然后启动 Reachy Mini app：

```bash
cd /home/tzhx/wyl/reachy_mini/reachy_dialogue_app
/home/tzhx/miniconda3/bin/conda run -n test python -m reachy_dialogue_app.main
```

如果你使用 Wireless，且 `reachy-mini.local` 解析失败，请改用机器人 IP：

```bash
/home/tzhx/miniconda3/bin/conda run -n test python -m reachy_dialogue_app.main --robot-host <机器人IP>
```

如果你使用 Lite，先确认本机 daemon 已经启动，或者让 app 自动启动 daemon：

```bash
/home/tzhx/miniconda3/bin/conda run -n test python -m reachy_dialogue_app.main --robot-host 127.0.0.1 --spawn-daemon
```

如果只是先用模拟环境验证界面和流程：

```bash
/home/tzhx/miniconda3/bin/conda run -n test python -m reachy_dialogue_app.main --robot-host 127.0.0.1 --spawn-daemon --use-sim
```

打开配置页：

```text
http://127.0.0.1:8042/
```

## 不连接机器人：web-only 文字输入和本机麦克风测试

如果只想验证文字对话、本机电脑麦克风、实时 STT、流式文本回复和流式 TTS 播放，
不需要启动 Reachy daemon，也不需要连接机器人：

```bash
cd /Users/xwan/code/reachy_mini/reachy_dialogue_app
REACHY_DIALOGUE_SERVICE_URL=http://127.0.0.1:12312 \
conda run -n toy python -m reachy_dialogue_app.main --web-only
```

然后打开：

```text
http://127.0.0.1:8042/
```

web-only 根页面会打开普通对话页，但隐藏机器人麦克风、机器人音量和机器人回放测试控件。
其中“手动输入”会调用本地 app 的 `/api/text-chat-stream`，再优先转发到对话服务
`/chat/stream`；如果后端启用长期记忆检索，web-only 文字输入也会透传
`retrieval_status`、`retrieved_memory_ids` 等返回字段。

本机麦克风测试页使用浏览器 `getUserMedia()` 读取本机麦克风，把音频降采样为
16kHz、16-bit、mono PCM，通过本地 app 代理发送到对话服务的 `/voice/live/*`
接口，停止录音后用 SSE 接收 `transcript` / `delta` / `audio` / `done`。
TTS `audio` chunk 会直接用浏览器扬声器播放。

本机麦克风测试页的 `/api/local-mic/finish-stream` 仍会请求
`/tools/voice-latency/finish-stream`，用于端到端延迟测试；正式机器人语音的
`/api/robot-mic/stop-stream` 使用 `/voice/live/finish-stream`，404 时才 fallback 到
`/voice/live/finish`。

如果你已经用普通机器人 app 启动了页面，也可以从机器人页面点击“本机麦克风测试”，
或直接打开：

```text
http://127.0.0.1:8042/static/local-mic-test.html
```

## 可选环境变量

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

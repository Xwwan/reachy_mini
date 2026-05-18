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
- 对话服务负责实时 STT、生成回复和 TTS；app 停止录音时调用 `/voice/live/finish`，不再把整段音频发到 `/voice/chat` 做二次识别。
- 对话服务返回 TTS PCM 后，app 会写成临时 WAV 并通过 `reachy_mini.media.play_sound()` 让机器人播放。
- 每轮回复默认触发摇头动作，页面里可改成天线摆动或不动作。

## 启动顺序

先启动你的对话系统：

```bash
cd /home/tzhx/test-project
/home/tzhx/miniconda3/bin/conda run -n test python -m src.main --host 127.0.0.1 --port 12312 --log-level DEBUG
```

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

## 可选环境变量

```bash
export REACHY_DIALOGUE_SERVICE_URL=http://127.0.0.1:12312
export REACHY_DIALOGUE_CONVERSATION_ID=reachy-mini-voice
export REACHY_DIALOGUE_GESTURE=shake_head
export REACHY_DIALOGUE_TTS_SAMPLE_RATE=24000
export REACHY_ROBOT_HOST=127.0.0.1
export REACHY_ROBOT_PORT=8000
export REACHY_SPAWN_DAEMON=true
export REACHY_USE_SIM=false
```

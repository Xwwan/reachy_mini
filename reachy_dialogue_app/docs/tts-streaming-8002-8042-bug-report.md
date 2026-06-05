# 8002 与 8042 流式 TTS 卡顿问题排查报告

## 1. 问题概述

在同一台设备、同一个 TTS 服务配置下，观察到以下现象：

- 通过 `8002` 的语音延迟测试页面播放 TTS 时，听感基本连续。
- 通过 `8042` 的 Reachy Dialogue `web-only` 页面播放 TTS 时，存在明显卡顿。
- 两条路径最终都会调用后端的 `_iter_dialogue_reply_stream_events()`，因此最初怀疑 TTS 分段规则、TTS 服务出块速度或播放器逻辑存在差异。

排查后确认，当前 `8042 web-only` 卡顿的主要问题位于 Reachy Dialogue 对后端 SSE 的代理解析环节：

```python
response.iter_lines(chunk_size=1, decode_unicode=True)
```

该代码按 1 字节读取和解析 SSE。一个 TTS `audio` 事件包含约 20KB 到 40KB 的 Base64 数据，在树莓派上逐字节处理会显著拖慢事件转发，使本来已经从后端到达的音频块约每 `0.49s` 才被转发一次。常见音频块自身只有 `0.32s`，因此浏览器播放库存会耗尽，产生真实断流。

## 2. 结论摘要

### 2.1 已确认的问题

`8042` 的 Interaction SSE 代理使用 `requests.Response.iter_lines(chunk_size=1)`：

```text
reachy_dialogue_app/reachy_dialogue_app/interaction/sse.py
```

这会对大型 Base64 音频事件造成明显 CPU 和迭代开销，是当前 `8042 web-only` 卡顿的主要原因。

诊断脚本也使用了相同写法：

```text
reachy_dialogue_app/scripts/dialogue_stream_probe.py
```

因此旧版 probe 会把客户端自身的逐字节解析耗时统计为服务端 chunk 到达间隔，产生约 `490ms` 的假慢数据。使用旧版 probe 直接测试 `8002`，同样会显示约 `490ms` 的 chunk 间隔，这不代表 `8002` 后端真的按该速度发送。

### 2.2 已排除的主要假设

1. **不是 8002 和 8042 使用了不同的后端 TTS 核心函数。**

   两条路径最终都进入：

   ```text
   test-project/src/api/routes.py::_iter_dialogue_reply_stream_events()
   ```

2. **不是用户提供的两段回复文本本身导致的主要差异。**

   将两段原文直接交给同一个 DashScope TTS 客户端时，后续 chunk 通常以约 `50ms` 到 `155ms` 的间隔到达，明显快于音频播放速度。

3. **不是 `robot_output.py` 中额外等待 `0.3s` 导致当前 8042 页面卡顿。**

   `robot_output.py` 只影响连接真实 Reachy 后的机器人扬声器播放。当前 8042 使用 `--web-only` 启动，声音由浏览器 `AudioContext` 播放，不执行该文件中的机器人播放逻辑。

### 2.3 仍需单独处理的风险

真实 Reachy 播放路径仍存在以下代码：

```python
time.sleep(max(0.3, playback_seconds - elapsed + 0.3))
```

路径：

```text
reachy_dialogue_app/reachy_dialogue_app/audio/robot_output.py
```

该逻辑会在每个机器人音频任务后预留额外时间，可能影响真实机器人播放连续性。但它与本次 `8042 web-only` 浏览器卡顿不是同一个问题，应另行测试和修改。

## 3. 8002 调用链

测试页面：

```text
http://127.0.0.1:8002/tools/voice-latency
```

前端文件：

```text
/home/tzhx/code/test-project/src/api/static/voice_latency.html
```

调用流程：

```text
浏览器 voice_latency.html
  -> POST /tools/voice-latency/finish-stream
  -> ApiRequestHandler._write_voice_latency_stream()
  -> iter_voice_latency_finish_stream()
  -> handle_chat_message_stream()
  -> _iter_dialogue_reply_stream_events()
  -> DashScopeTtsClient.synthesize_stream()
  -> 后端直接写出 SSE audio 事件
  -> 浏览器 fetch Response.body.getReader()
  -> AudioPlaybackScheduler 连续播放 PCM chunk
```

相关后端代码：

| 功能 | 路径与位置 |
|---|---|
| 8002 语音延迟流入口 | `test-project/src/api/routes.py::iter_voice_latency_finish_stream` |
| HTTP SSE 写出入口 | `test-project/src/api/routes.py::_write_voice_latency_stream` |
| 共用 TTS 流处理 | `test-project/src/api/routes.py::_iter_dialogue_reply_stream_events` |
| TTS 文本分段 | `test-project/src/api/routes.py::_StreamingTtsSegmenter` |
| DashScope TTS WebSocket | `test-project/src/audio/dashscope_tts.py::DashScopeTtsClient` |
| 浏览器流读取和播放 | `test-project/src/api/static/voice_latency.html` |

关键特征：

- 后端产生 SSE 后直接发送给浏览器。
- 浏览器使用 `fetch()` 和 `Response.body.getReader()` 读取网络流。
- 不经过 Python `requests.iter_lines()` 代理解析。
- 浏览器播放器会排队保存已收到的音频块，上一块结束后立即播放下一块。

## 4. 8042 调用链

测试页面：

```text
http://127.0.0.1:8042/
```

本次使用的启动方式：

```bash
REACHY_DIALOGUE_SERVICE_URL=http://127.0.0.1:8002 \
conda run -n reachy_mini python \
  -m reachy_dialogue_app.reachy_dialogue_app.main \
  --web-only \
  --web-host 0.0.0.0 \
  --web-port 8042
```

调用流程：

```text
浏览器 main.js
  -> POST 8042 /api/interaction/text-stream
  -> interaction_routes.py::interaction_text_stream()
  -> InteractionApiClient.text_stream()
  -> POST 8002 /interaction/runs/text-stream
  -> 8002 iter_text_interaction_events()
  -> 8002 _iter_dialogue_reply_stream_events()
  -> DashScopeTtsClient.synthesize_stream()
  -> 8002 写出 SSE audio 事件
  -> 8042 requests.Response.iter_lines()
  -> 8042 重新编码并转发 SSE
  -> 浏览器 main.js 接收 audio 事件
  -> 浏览器 AudioContext 播放 PCM chunk
```

相关代码：

| 功能 | 路径与位置 |
|---|---|
| 8042 浏览器请求入口 | `reachy_dialogue_app/reachy_dialogue_app/static/main.js::sendText` |
| 8042 FastAPI SSE 代理 | `reachy_dialogue_app/reachy_dialogue_app/api/interaction_routes.py::interaction_text_stream` |
| 请求 8002 Interaction 接口 | `reachy_dialogue_app/reachy_dialogue_app/interaction/client.py::InteractionApiClient.text_stream` |
| 解析 8002 SSE | `reachy_dialogue_app/reachy_dialogue_app/interaction/sse.py::iter_sse_events` |
| 8042 浏览器 PCM 播放 | `reachy_dialogue_app/reachy_dialogue_app/static/main.js::playBrowserPcmChunk` |
| 8002 Interaction SSE 入口 | `test-project/src/api/routes.py::_write_interaction_text_stream` |
| 共用 TTS 流处理 | `test-project/src/api/routes.py::_iter_dialogue_reply_stream_events` |

与 8002 页面相比，8042 多出了一层 Python SSE 代理：

```text
8002 SSE -> requests.iter_lines() -> JSON 解码 -> JSON 重编码 -> 8042 SSE
```

问题发生在这层代理的逐字节读取，而不是共用的 TTS worker。

## 5. 为什么 `chunk_size=1` 会导致卡顿

SSE 的 `audio` 事件包含 Base64 PCM：

```json
{
  "audio_base64": "...",
  "sample_rate": 24000,
  "chunk_index": 1,
  "segment_index": 0
}
```

常见 PCM 大小：

| PCM 字节数 | 音频时长 | Base64 后大约大小 |
|---:|---:|---:|
| 15360 bytes | 320ms | 约 20KB |
| 23040 bytes | 480ms | 约 30KB |
| 30720 bytes | 640ms | 约 40KB |

使用以下代码时：

```python
response.iter_lines(chunk_size=1, decode_unicode=True)
```

`requests` 每次只向内部行解析器提供 1 字节。一个约 20KB 的 SSE `data:` 行需要经历约两万次 Python 层处理，之后才能识别完整行并向上层 yield。对于流式音频，这种开销会直接表现为音频块转发延迟。

建议候选修改：

```python
response.iter_lines(chunk_size=8192, decode_unicode=True)
```

或实现专门的增量 SSE 解析器，以较大网络块读取，同时按 SSE 空行边界拆帧。

## 6. 实验记录

### 6.1 修复前的 8042 典型数据

旧版 8042/probe 使用 `chunk_size=1` 时：

```text
320ms 音频块：常见到达间隔约 480ms 到 530ms
480ms 音频块：部分到达间隔约 940ms 到 960ms
```

示例：

| chunk 音频时长 | 到达间隔 | 结果 |
|---:|---:|---|
| 320ms | 490ms | 约缺音 170ms |
| 320ms | 506ms | 约缺音 186ms |
| 480ms | 951ms | 约缺音 471ms |

### 6.2 候选修改验证

实验性地将以下两个位置从 `chunk_size=1` 调整为较大的读取块：

```text
reachy_dialogue_app/reachy_dialogue_app/interaction/sse.py
reachy_dialogue_app/scripts/dialogue_stream_probe.py
```

修改前后一次实测对比：

| 指标 | 修改前 | 实验修改后 |
|---|---:|---:|
| 8042 总耗时 | 约 48.5s | 约 8.7s |
| audio chunk 平均到达间隔 | 约 453ms | 约 106ms |
| 常见 320ms chunk 到达间隔 | 约 480ms 到 500ms | 约 40ms 到 75ms |
| 最终估算音频库存 | 约 0.36s | 约 10.7s |

实验修改后，大部分音频块到达速度明显快于其播放时长，浏览器能够积累播放库存。

### 6.3 实验限制

- LLM 每次回复内容和长度不同，因此总 chunk 数与总音频时长不可直接一一比较。
- 修改前后的核心判断依据应是“chunk 到达间隔相对 chunk 音频时长”，而不是仅比较总耗时。
- 旧版 probe 自身也存在 `chunk_size=1` 问题，因此旧版 probe 对直接 8002 的测量会产生客户端解析延迟。
- 最严谨的后续验证方式是固定同一段 PCM/SSE 流，分别经过不同解析配置进行基准测试。

## 7. 分段规则与本问题的关系

后端 TTS 分段代码：

```text
test-project/src/api/routes.py::_StreamingTtsSegmenter
```

分段规则会影响：

- 何时开始调用 TTS。
- 每个 TTS WebSocket 会话接收多少文字。
- 不同 segment 之间是否需要重新建立 TTS 会话。

但本次卡顿不是单纯由分段规则解释的：

- 8002 和 8042 最终共用相同的 `_iter_dialogue_reply_stream_events()` 和 `_StreamingTtsSegmenter`。
- 问题数据中，同一个 segment 内连续 chunk 也出现约 `490ms` 的固定延迟。
- 调整 8042 SSE 读取块大小后，在不修改后端分段规则的情况下，chunk 间隔显著下降。

因此，分段规则可能影响首包延迟和 segment 间隙，但不是当前 8042 `web-only` 持续卡顿的主要根因。

## 8. 播放库存与“听起来是否卡”

判断流式音频是否卡顿，不能只看两个 chunk 的到达间隔，还需要比较：

```text
当前已有播放库存 + 新 chunk 音频时长
```

例如：

- 每个 chunk 包含 `320ms` 音频。
- 如果 chunk 每 `60ms` 到达一次，播放器会快速积累库存，后续即使出现 `600ms` 间隔，也可能听不出断流。
- 如果 chunk 每 `490ms` 到达一次，且库存始终只有一个 `320ms` chunk，则每块之间会缺约 `170ms` 音频，听感必然卡顿。

建议持续记录：

- `duration_ms`
- `interval_from_previous_ms`
- `estimated_backlog_ms`
- `estimated_starvation_ms`
- `segment_index`
- `chunk_index`

## 9. 建议的手动修改步骤

### 9.1 修改 8042 生产代理

文件：

```text
/home/tzhx/code/reachy_mini/reachy_dialogue_app/reachy_dialogue_app/interaction/sse.py
```

候选修改：

```diff
- for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
+ for raw_line in response.iter_lines(chunk_size=8192, decode_unicode=True):
```

### 9.2 修改诊断 probe

文件：

```text
/home/tzhx/code/reachy_mini/reachy_dialogue_app/scripts/dialogue_stream_probe.py
```

候选修改：

```diff
- for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
+ for raw_line in response.iter_lines(chunk_size=8192, decode_unicode=True):
```

否则 probe 自身仍会制造延迟，无法准确测量服务端事件到达时间。

### 9.3 增加回归测试

建议构造一个假的 `requests.Response`：

- 连续发送多个约 20KB 的 SSE `audio` 事件。
- 记录事件从底层响应到 `iter_sse_events()` yield 的时间。
- 验证代理不会因为逐字节解析而使每个事件产生数百毫秒延迟。
- 验证跨读取块边界的 SSE 行仍能被正确拼接。

### 9.4 修改后验证

通过 8042 执行 probe：

```bash
cd /home/tzhx/code/reachy_mini

conda run -n reachy_mini python \
  reachy_dialogue_app/scripts/dialogue_stream_probe.py \
  --label interaction-test \
  --app-url http://127.0.0.1:8042 \
  --text "请用一句话回答：今天适合和 Reachy Mini 聊些什么？" \
  --print-chunks \
  --output /tmp/interaction-test.json \
  --chunks-csv /tmp/interaction-test.csv \
  --save-audio /tmp/interaction-test.wav
```

重点检查：

```text
大部分 interval_from_previous_ms < 对应 duration_ms
estimated_backlog_ms 能持续增长或保持正数
estimated_starvation_ms 不持续累计
浏览器实际播放无规律性断流
```

## 10. 本次排查中的代码状态

本次最终代码状态：

- `robot_output.py` 的实验修改已退回，本次不处理真实机器人扬声器播放路径。
- `interaction/sse.py` 已保留 `chunk_size=8192` 修改，用于降低 8042 SSE 代理解析延迟。
- `dialogue_stream_probe.py` 已同步使用 `chunk_size=8192`，避免诊断工具自身制造逐字节解析延迟。
- 诊断工具保留直接测试后端、输出 chunk 明细、保存 CSV/WAV 等辅助排查能力。

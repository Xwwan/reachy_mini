# Reachy Dialogue App New Backend Integration Plan

## 当前分支

`feature/reachy-dialogue-interaction-api`

## 我对需求的理解

你已经大幅修改了对话后端，新的后端契约记录在：

`/Users/xwan/code/test-project/docs/api_contracts.md`

这次 `reachy_dialogue_app` 不再继续兼容旧前端和旧业务接口。目标是把它改造成一个面向新统一 Interaction API 的原生 Reachy Mini 客户端：

- 可以完全抛弃现有 `static/` 前端实现。
- 不需要 fallback 到旧接口。
- 主链路只接入新的 `/interaction/*` API。
- 本地 Reachy app 仍负责机器人麦克风采集、机器人扬声器播放、行为标签触发、音量控制和本地调试。
- 新后端负责统一 session、run、workflow、文字/语音 interaction、onboarding 状态和播放状态记录。

## 新后端主契约

本轮接入以这些接口为核心：

- `POST /interaction/sessions`
- `GET /interaction/sessions/{interaction_session_id}`
- `GET /interaction/sessions/{interaction_session_id}/runs`
- `GET /interaction/runs/{run_id}`
- `POST /interaction/runs/text-stream`
- `POST /interaction/live/start`
- `POST /interaction/live/chunk`
- `GET /interaction/live/transcript`
- `POST /interaction/live/finish-stream`
- `POST /interaction/live/abort`
- `POST /interaction/playback/done`
- `POST /interaction/playback/error`

旧接口不再作为主路径，也不做 fallback：

- 不再 fallback 到 `/chat`。
- 不再 fallback 到 `/chat/stream`。
- 不再 fallback 到 `/voice/chat`。
- 不再 fallback 到 `/voice/live/*`。
- 不再 fallback 到 `/tools/voice-latency/finish-stream`。

`/followups/*` 和 `/memory/*` 是否继续保留为辅助功能，需要根据新后端实际产品目标确认；它们不会作为 initial chat/voice 主链路的基础。

## 技术方案

### 1. 新增 Interaction Client 层

新增一个集中封装新后端的模块，避免在 `main.py` 和音频模块里散落 URL 拼接和 SSE 解析。

建议结构：

- `reachy_dialogue_app/interaction/__init__.py`
- `reachy_dialogue_app/interaction/client.py`
- `reachy_dialogue_app/interaction/types.py`
- `reachy_dialogue_app/interaction/sse.py`

这个 client 负责：

- 统一 normalize service URL。
- 创建 interaction session。
- 查询 session 和 runs。
- 发起 text stream。
- 管理 live start/chunk/transcript/finish/abort。
- 发送 playback done/error。
- 解析统一错误格式：`{"error": {"message": "..."}}`。
- 解析并透传 SSE 事件。

### 2. 重写本地 FastAPI 代理层

保留 Reachy app 的本地 FastAPI 服务，但把现有 `/api/text-chat-stream`、`/api/robot-mic/*`、`/api/auto-voice/*` 等旧语义接口逐步替换或重接到 Interaction API。

建议新的本地 API：

- `POST /api/interaction/session`
- `GET /api/interaction/session`
- `GET /api/interaction/runs`
- `GET /api/interaction/runs/{run_id}`
- `POST /api/interaction/text-stream`
- `POST /api/interaction/live/start`
- `POST /api/interaction/live/chunk`
- `GET /api/interaction/live/transcript`
- `POST /api/interaction/live/finish-stream`
- `POST /api/interaction/live/abort`

机器人麦克风可以保留更贴近设备的本地入口，但内部必须走新后端：

- `POST /api/robot-mic/start-interaction`
- `POST /api/robot-mic/finish-interaction-stream`
- `GET /api/robot-mic/level`
- `GET /api/robot-mic/debug`

### 3. 重写播放调度与后端播放状态闭环

新后端的 `audio` 事件会带：

- `playback_key`
- `run_id`
- `interaction_session_id`
- `workflow`

机器人播放队列必须优先使用后端给出的 `playback_key`，而不是只靠 `request_id + turn_id` 推导。

播放组需要保存：

- `playback_key`
- `run_id`
- `interaction_session_id`
- `workflow`
- `audio chunks`
- `done_event`
- `action_signal`
- `action_config`

播放完成后：

- 成功：调用 `POST /interaction/playback/done`
- 失败：调用 `POST /interaction/playback/error`

本地前端仍可收到 `playback_done` 或 `playback_error` SSE，用于 UI 展示；但真实状态以新后端 run 的 `playback_status` 为准。

### 4. 文本交互主路径

文本输入流程改为：

1. 前端选择或创建 interaction session。
2. 本地 app 调用 `POST /interaction/runs/text-stream`。
3. 本地 app 透传这些 SSE：
   - `meta`
   - `delta`
   - `audio`
   - `state_delta`
   - `done`
   - `error`
4. `audio` 事件同时进入机器人播放调度。
5. `done` 到达后，当前播放组标记 completed。
6. 播放实际结束后，回调新后端 playback done/error。

`workflow=chat` 时，`done` 可能带 `request_id` 和 `retrieval_status=pending`。

`workflow=onboarding` 时，`done` 可能带：

- `onboarding_session_id`
- `stage`
- `stage_name`
- `collected`
- `missing_required_slots`
- `onboarding_complete`

### 5. 机器人麦克风语音交互

机器人麦克风流程改为：

1. 创建或复用 interaction session。
2. `POST /interaction/live/start`，得到 `live_session_id`。
3. 后端持续读取 Reachy 麦克风，按 16kHz、16-bit、mono PCM 分块。
4. 每个 chunk 发到 `POST /interaction/live/chunk`。
5. UI 轮询或订阅本地接口，本地接口查询 `GET /interaction/live/transcript`。
6. 停止录音时调用 `POST /interaction/live/finish-stream`。
7. 透传 `transcript/meta/delta/audio/state_delta/done/error`。
8. `audio` 进入机器人播放调度。
9. 播放完成后回调 playback done/error。

### 6. 本地麦克风与自动语音

本地麦克风和自动语音也应使用同一套 Interaction API。

本地麦克风：

- 浏览器采集音频。
- 本地 app 代理到 `/interaction/live/*`。
- 不再走旧 `/voice/live/*`。

自动语音：

- 仍可保留本地 Silero VAD。
- VAD 只负责分句和半双工状态机。
- 每次真实用户 utterance 使用 `/interaction/live/start`、`/interaction/live/chunk`、`/interaction/live/finish-stream`。
- 助手 speaking/playback 期间暂停新 utterance。
- 播放完成后恢复 listening。

唤醒门禁建议：

- 唤醒词本地处理，不创建 interaction run。
- 已唤醒后的真实用户问题才进入 Interaction API。
- 这样不会把唤醒词污染到 chat/onboarding 历史里。

### 7. 全新前端

现有前端可以完全废弃。新前端建议从零组织为一个简洁的 interaction 工作台：

- 顶部设置区：
  - service URL
  - workflow：`chat` / `onboarding`
  - conversation id
  - interaction session id
  - session 状态

- 主对话区：
  - user transcript
  - assistant streaming delta
  - final done payload
  - onboarding state delta

- 输入区：
  - 文本输入
  - 本地麦克风
  - 机器人麦克风
  - 自动语音开关

- 运行状态区：
  - current run id
  - current playback key
  - playback status
  - live session id
  - latest transcript

- 调试区：
  - raw SSE events
  - recent runs
  - backend errors
  - playback done/error callback result

前端播放调度如果保留浏览器本地播放，也必须使用同样规则：

1. 优先使用 `playback_key`。
2. 其次才使用 `request_id + turn_id` 或 `run_id`。
3. 不用 `chunk_index` 做全局排序。
4. 同一播放组内按 `segment_index/chunk_index/arrival_index` 排序。

### 8. 行为标签与机器人动作

保留现有行为标签机制：

- `[emo:...]`
- `[act:...]`

触发时机建议仍在本地 app 处理：

- `done.reply` 到达后解析行为标签。
- 对 `audio` 播放和动作触发做顺序协调。
- 动作失败不应阻断 playback done/error 回调，但需要记录到本地 SSE/debug。

### 9. 测试策略

新增或重写测试，重点覆盖新契约：

- interaction session 创建。
- text stream 透传 `meta/delta/audio/state_delta/done`。
- audio 使用 `playback_key` 入队。
- `run_id + playback_key` 被保存到播放组。
- 播放成功后调用 `/interaction/playback/done`。
- 播放失败后调用 `/interaction/playback/error`。
- live start/chunk/transcript/finish-stream 全链路。
- `workflow=chat` 时保留 `request_id/retrieval_status`。
- `workflow=onboarding` 时正确透传 onboarding 状态，不强依赖 `request_id`。
- 前端 mock 测试覆盖 `playback_key` 优先级和 `state_delta` 渲染。

运行测试默认使用项目约定的 conda 环境：

```bash
conda run -n toy python -m pytest tests/unit_tests/test_reachy_dialogue_streams.py
```

必要时再扩展到相关测试集。

## 实施顺序

1. 写好本计划并确认问题。
2. 新增 Interaction Client 层。
3. 重写播放调度 metadata 和 playback done/error 回调。
4. 重接文本 interaction stream。
5. 重接机器人麦克风 live interaction。
6. 重接本地麦克风 live interaction。
7. 重接自动语音。
8. 从零替换前端静态文件。
9. 更新 README。
10. 重写/新增测试。
11. 运行核心测试并修正问题。

## 暂不做的事

- 不保留旧前端结构。
- 不做旧 `/chat`、`/voice/live`、`/voice/chat` 的 fallback。
- 不自动启动 `/Users/xwan/code/test-project` 后端服务。
- 不提交任何 API key、token、数据库或私有数据。
- 不发布 Hugging Face Space。

## 待确认问题

请在下面填写答案，或直接在聊天里回复：

1. 默认 workflow 是 `chat` 还是 `onboarding`？
   - 答案：onboarding 和 chat 可使用按钮进行切换

2. 是否需要在第一版 UI 中完整支持 onboarding 阶段展示和字段收集，还是只先透传 `state_delta` 和 `done` 中的 onboarding 状态？
   - 答案：完整支持 onboarding 阶段展示和字段收集

3. 是否保留 follow-up 面板和 `/followups/*` 辅助功能？
   - 答案：保留

4. 是否保留 memory curate/profile refresh 调试按钮？
   - 答案：是

5. 自动语音的唤醒门禁是否继续保留？
   - 答案：是

6. 机器人播放完成后，是否必须等待 `/interaction/playback/done` 成功返回，才向前端发送本地 `playback_done`？
   - 答案：是

7. 新前端是否需要继续支持 web-only 模式，也就是没有真实 Reachy 时使用浏览器本地麦克风和本地音频播放调试？
   - 答案：是

8. 默认服务地址是否改成新后端实际地址？
   - 当前旧默认：`http://127.0.0.1:12312`
   - 答案：是

## 备注

本计划描述的是一次破坏性重接。后续实现时可以删除或替换旧前端代码和旧接口代理逻辑，但每一步都应保持测试可运行，并避免改动与 `reachy_dialogue_app` 无关的项目区域。

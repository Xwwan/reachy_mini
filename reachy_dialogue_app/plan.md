# Reachy Dialogue App Plan

## 我对需求的理解

你已经在 `/home/tzhx/test-project` 写好了一个本地长期记忆对话系统，希望在 Reachy Mini 项目下使用它，并且 Python 运行环境使用 conda 环境 `test`。

当前我建议把它接成一个 Reachy Mini Python app：Reachy app 负责连接机器人、提供简单控制/对话入口、触发机器人动作；你的对话系统继续作为独立本地 HTTP 服务运行。这样可以避免两个项目都使用顶层包名 `src` 时出现 import 冲突，也便于单独测试对话系统。

## 技术方案

1. 使用 `reachy-mini-app-assistant` 创建的本地 app 骨架：`reachy_dialogue_app/`。
2. 保留 `/home/tzhx/test-project` 作为独立服务，由下面命令启动：
   ```bash
   /home/tzhx/miniconda3/bin/conda run -n test python -m src.main --host 127.0.0.1 --port 8000 --log-level DEBUG
   ```
3. 在 Reachy app 中增加一个轻量 HTTP client，调用：
   - `GET /healthz` 检查对话服务是否在线
   - `POST /chat` 发送文本并拿到 `reply`
   - 可选：`POST /voice/chat` 接入语音输入/输出
4. 在 Reachy app 的 `static/` 页面中提供一个手机友好的聊天界面。
5. Reachy app 收到回复后可执行基础表现动作，例如点头、轻微转头、天线摆动；是否朗读回复取决于你希望使用文本对话还是语音对话。

## 待确认问题

请在下面填写答案，或直接在聊天里回复：

1. 机器人类型：
   - 答案：reachy mini

2. 你想先做哪种交互？
   - A. 文本聊天：网页输入文字，机器人做动作，可先不说话
   - B. 语音聊天：使用 `/voice/chat`，机器人需要播放 TTS 音频
   - 答案：语音聊天

3. 对话服务是否由你手动单独启动，还是希望 Reachy app 自动启动 `/home/tzhx/test-project` 服务？
   - 答案：先是我手动启动

4. 默认服务地址是否固定为 `http://127.0.0.1:8000`？
   - 答案：自定义地址（默认12312），可修改

5. 每轮回复后希望机器人做什么动作？
   - 示例：点头、摇头、看向用户、天线摆动、随机情绪动作
   - 答案：摇头（可修改）

## 暂不做的事

- 暂不把 `/home/tzhx/test-project` 复制进 Reachy app，避免重复维护。
- 暂不发布到 Hugging Face，先在本机跑通。
- 暂不提交 `.env`、数据库或任何 API key。

## 实现记录

- 已实现语音聊天桥接页面，默认对话服务地址为 `http://127.0.0.1:12312`。
- 已实现 `/api/voice-chat` 转发到外部对话系统 `/voice/chat`。
- 已实现 TTS PCM 转临时 WAV，并通过 Reachy Mini 媒体接口播放。
- 已移除 dialogue app 的默认摇头/天线动作；动作由 `[act:...]` 等行为标签触发，并复用当前 ReachyMini 连接调用 `action_call` 函数。
- 临时加入机器人麦克风回放测试：页面可单独录制机器人麦克风音频，停止后不经过对话服务，直接用机器人扬声器播放，用于检查麦克风输入和扬声器输出。
- 加入 Reachy Emoji 联动：dialogue app 最初维护 `emoji_config.json` 表示可用表情和 `signal_map`；当回复中出现配置 key 时，通过 URL 请求触发表情服务。
- 本次调整计划：把表情联动升级为通用行为标签联动。配置从 JSON 迁移到 `behavior_config.yaml`，由 YAML 声明模块、可识别 tag 名、触发 key 白名单或 `*`、触发方式。dialogue app 只解析模型回复里的 `[tag:key]`；表情继续通过 URL 触发，动作暂时直接调用 `action_call` 函数且不在 dialogue app 内做映射；前端继续显示原始 tag 文本，只额外展示触发状态。
- 本次调整计划：增加手动文本输入入口，复用对话服务 `/chat` 和现有行为标签/机器人播放队列；增加扬声器与麦克风音量滑杆，通过 Reachy daemon 的 `/api/volume/*` 接口代理读写音量。
- 本次调整计划：把现有页面升级为完整的“实时首回复 + 异步记忆补充”对话前端。用户已确认可以做完整改造，不限 MVP；后端接口已具备但当前服务不一定启动；文本和语音回复都进入同一条聊天时间线；保留文本朗读开关并默认开启；切换 `conversation_id` 不清空当前时间线；需要开发者调试面板。
- 实现方向：新增 `/api/followups/stream` 同源代理，前端建立独立 `EventSource` 监听 follow-up；聊天状态按 `request_id` / message id 绑定，不按“最后一条消息”追加；`/chat/stream` 与机器人麦克风 `stop-stream` 都复用同一套 timeline 渲染；follow-up 作为独立补充/修正消息插入，避免和当前 streaming 回复串线；调试面板展示 follow-up 连接状态、最近 request、原始 payload 和可选记忆维护动作。
- 本次调整计划：新增统一 Silero VAD 自动对话模式。web-only 电脑麦克风和真实 Reachy 机器人麦克风都使用后端 Silero VAD 做自动分句；模型文件放在 `reachy_dialogue_app/models/`，默认不提交 ONNX 二进制，提供下载脚本和 README。新增 `/api/auto-voice/*`：local 模式由浏览器持续上传 PCM chunk，robot 模式由后端读取机器人麦克风；两者共用自动会话状态机、SSE 事件和现有 timeline 渲染。第一版半双工：助手流式回答/播放期间暂停触发新 utterance，等 `playback_done`/cooldown 后恢复 listening；保留原手动录音、本机麦克风测试和机器人麦克风回放测试。
- 本次调整计划：在自动对话外层增加唤醒门禁状态机。用户已确认走路径 A，后端新增 `/voice/live/finish-transcript`，用于结束实时语音识别会话并只返回最终识别文本；自动语音 session 在 `wake_gate.enabled` 时先用该接口判断唤醒词/退出词，只有已唤醒且不是退出词的语句才把最终文本送入 `/chat/stream`（fallback `/chat`）生成回复和 TTS。默认体验：唤醒词单独一句唤醒，唤醒后连续对话，退出词或空闲超时回到等待唤醒；配置放在 `behavior_config.yaml` 的 `wake_gate` 段。

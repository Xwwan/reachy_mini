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
- 已实现默认摇头动作，可在页面改为天线摆动或不动作。
- 临时加入机器人麦克风回放测试：页面可单独录制机器人麦克风音频，停止后不经过对话服务，直接用机器人扬声器播放，用于检查麦克风输入和扬声器输出。
- 加入 Reachy Emoji 联动：dialogue app 维护 `emoji_config.json` 表示可用表情和 `signal_map`；当回复中出现配置 key 时，通过 URL 请求触发表情服务。

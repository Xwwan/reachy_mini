# 前端工作交接总结

本文记录本次会话中完成的 Reachy Mini 本地应用切换前端，以及当前状态。下次新会话继续工作时，建议先阅读本文和 `frontend/README.md`。

## 背景目标

项目根目录是 Reachy Mini fork，但当前需求只关注：

- `apps/`：本地/第三方/自研应用目录。
- `frontend/`：本次新建的应用切换前端。

目标是做一个前端应用，可以列出 `apps/` 下的应用，并实际启动、停止、切换这些 app。由于团队有自研应用，不希望依赖 Reachy Mini 官方 daemon app registry，所以最终采用了本项目自己的本地 App Manager。

## 当前架构

```text
浏览器前端
  -> Vite dev server
      -> /api 代理到本地 App Manager
          -> frontend/server/app-manager.mjs
              -> 扫描 ../apps
              -> 读取 reachy-app.json
              -> 使用共享 conda 环境或创建/检测 app 专属 .venv
              -> 启动/停止单个 app 子进程
                  -> app 自己连接 Reachy Mini daemon 或其他后端
```

Reachy Mini daemon 仍然用于机器人硬件控制，但前端不再调用官方 `/api/apps/*` 来管理应用。

## 主要文件

- `frontend/server/app-manager.mjs`：Node 本地 App Manager API。
- `frontend/src/App.tsx`：React 主界面，列表、Setup、Start、Stop、Restart、iframe。
- `frontend/src/api.ts`：前端 API client。
- `frontend/src/types.ts`：前端类型定义。
- `frontend/src/styles.css`：界面样式。
- `frontend/README.md`：中文使用说明。
- `frontend/plan.md`：方案演进记录。

## 运行方式

需要两个终端。

终端 1：

```bash
cd /home/shivice/code/reachy_mini/frontend
npm run server
```

终端 2：

```bash
cd /home/shivice/code/reachy_mini/frontend
npm run dev
```

打开 Vite 输出的地址，通常是：

```text
http://localhost:5173/
```

如果端口被占用，Vite 会自动换到 `5174` 等端口。

## App Manager API

当前支持：

- `GET /api/local-apps`
- `GET /api/local-apps/current`
- `POST /api/local-apps/{app_id}/setup`
- `GET /api/local-apps/{app_id}/setup-status`
- `POST /api/local-apps/{app_id}/start`
- `POST /api/local-apps/current/stop`
- `POST /api/local-apps/{app_id}/restart`

同一时间只运行一个 app。启动新 app 前会先停止当前 app。

## reachy-app.json 协议

当前推荐使用结构化字段，而不是手写完整 command：

```json
{
  "id": "my_app",
  "title": "My App",
  "description": "显示在切换器中的简短说明。",
  "environment": "shared",
  "python": "python",
  "venv": null,
  "module": "my_app.main",
  "args": [],
  "frontendUrl": "http://127.0.0.1:8042/",
  "healthUrl": "http://127.0.0.1:8042/health",
  "stopSignal": "SIGINT",
  "setup": {
    "install": ["-e", "."]
  },
  "setupHint": "首次运行前请安装依赖。",
  "env": {}
}
```

行为：

- `environment: "shared"`：使用启动 App Manager 时已激活的主 conda/Python 环境。`Start` 默认可用，`Install deps` 会执行 `python -m pip install ...setup.install` 安装到当前环境。
- `environment: "venv"`：使用 app 独立 `.venv`。如果 `venv` 指定的 `.venv` 不存在，前端显示 `Setup`，禁用 `Start`。
- 点击 `Setup` 后，App Manager 会：
  - 执行 `python3 -m venv .venv`
  - 执行 `.venv/bin/python -m pip install -U pip`
  - 执行 `.venv/bin/python -m pip install ...setup.install`
- 安装成功后，`Start` 可用。
- `Start` 不会偷偷安装依赖。
- 安装失败后，前端显示 `setup failed`，禁用 `Start`，保留最近 setup 日志，并允许再次点击 `Setup` / `Install deps` 重试。

也保留了 `command` 兼容字段：如果 descriptor 提供 `command`，App Manager 会优先使用它。

环境管理建议：

- 当前倾向先验证共享主 conda 环境是否能容纳所有常用 app 依赖；验证通过后，自研/常用 app 可以使用 `environment: "shared"`。
- 依赖冲突明显、第三方 demo 或需要隔离的 app 继续使用 `environment: "venv"`。
- 使用共享环境时，必须先激活主 conda 环境，再运行 `npm run server`，否则 `python`/`pip` 可能指向错误环境。
- 验证共享环境时建议先在临时 conda 环境里逐个 `pip install -e ...`，最后执行 `python -m pip check`。

## 已接入应用

目前这些应用已有 `reachy-app.json`：

- `apps/clawbody/reachy-app.json`
  - `id`: `clawbody`
  - venv: `apps/clawbody/.venv`
  - module: `reachy_mini_openclaw.main`
  - args: `--gradio`
  - frontend: `http://127.0.0.1:7860/`
  - setup: `pip install -e ".[mediapipe_vision]"`

- `apps/cookAIware/reachy-app.json`
  - `id`: `cookAIware`
  - venv: `apps/cookAIware/.venv`
  - module: `cookAIware.main`
  - frontend: `http://127.0.0.1:7860/`
  - setup: `pip install -e .`

- `apps/reachy-dance-duo/reachy-app.json`
  - `id`: `reachy_dance_duo`
  - venv: `apps/reachy-dance-duo/.venv`
  - module: `reachy_dance_duo`
  - args: `--host 0.0.0.0 --port 9000`
  - frontend: `http://127.0.0.1:9000/`
  - setup: `pip install -e .`

- `apps/reachy_mirror/reachy-app.json`
  - `id`: `reachy_mirror`
  - environment: `shared`
  - module: `reachy_mirror.main`
  - frontend: `http://127.0.0.1:7860/`
  - health: `http://127.0.0.1:7860/ready`
  - setup: `pip install -e .`
  - 主要依赖：`opencv-python`、`numpy`、`mediapipe==0.10.14`

- `reachy_dialogue_app/reachy-app.json`
  - 通过软链接接入：`apps/reachy_dialogue_app -> ../reachy_dialogue_app`
  - `id`: `reachy_dialogue_app`
  - venv: `apps/reachy_dialogue_app/.venv`，实际指向 `reachy_dialogue_app/.venv`
  - module: `reachy_dialogue_app.reachy_dialogue_app.main`
  - args: `--robot-host 127.0.0.1 --spawn-daemon`
  - frontend: `http://127.0.0.1:8042/`
  - setup: `pip install -e .`

注意：`clawbody`、`cookAIware` 和 `reachy_mirror` 都使用 7860 端口，但 App Manager 同一时间只运行一个 app，正常切换时不会冲突。

## 已验证内容

已完成：

- `npm run build` 通过。
- App Manager 能扫描 `apps/` 并返回 4 个 app。
- 未 setup 时，API 返回 `setup.state: needed`，`startable: false`。
- 未 setup 时调用 Start，会返回错误：需要先 setup。
- `setup-status` 接口可用。

未实际执行完整 Setup 安装，因为这些 app 依赖较重，需要网络下载和可能的系统库支持。用户后续可在浏览器里点击 `Setup` 逐个安装。

## 当前功能状态

前端当前具备：

- 本地 App Manager 地址配置。
- Dev proxy 开关。
- app 列表。
- app setup 状态展示。
- Setup / Install deps 按钮。
- Setup 日志展示。
- Start / Stop / Restart。
- 当前 app 状态展示。
- app 自带页面 iframe 嵌入。
- app 页面新窗口打开。
- 中文 README。

## 后续建议

优先级较高：

- 给 Setup 增加取消能力。
- 给 Setup 日志增加更完整的查看/复制体验。
- 支持安装失败后的 retry/clear 状态。
- 给 App Manager 增加健康检查接口，比如 `GET /api/health`。
- 在前端更明确地区分 `not configured`、`needs setup`、`setup failed`、`ready`。

可选优化：

- 对 `healthUrl` 做轮询，判断 app 页面是否真正起来。
- 支持 app descriptor 中声明端口冲突策略。
- 支持自定义 `.env` 检查，例如提示缺 `OPENAI_API_KEY`。
- 支持 setup 使用 `uv` 或 conda，但默认仍建议每 app 独立 `.venv`。

## 重要注意

- 不要把所有 app 依赖安装到全局 Python 环境。
- 当前推荐优先验证共享主 conda 环境；验证通过的 app 可用 `environment: "shared"`，冲突 app 再用独立 `.venv`。
- App Manager 是 Node 脚本，不需要 Python/FastAPI 依赖。
- App Manager 的提升权限只用于本地监听端口和访问本机 API；正常用户终端运行不需要特殊处理。

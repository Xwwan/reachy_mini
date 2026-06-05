# Reachy Mini 应用切换前端

这是本项目里的本地应用切换器。它不依赖 Reachy Mini 官方 daemon
的应用注册表来列出或启动应用，而是调用本项目自己的本地 App Manager：

```text
frontend/server/app-manager.mjs
```

整体运行关系：

```text
浏览器前端
  -> 本地 App Manager API: http://127.0.0.1:8787
      -> 扫描 ../apps
      -> 同一时间启动/停止一个本地 app 进程
          -> app 自己按需连接 Reachy Mini daemon 或其他后端服务
```

Reachy Mini daemon 仍然可以被各个 app 用来控制机器人硬件；它只是不再负责
“应用发现”和“应用切换”。

## 运行

需要两个终端。

终端 1：启动本地 App Manager。

```bash
cd /home/shivice/code/reachy_mini/frontend
npm run server
```

终端 2：启动前端开发服务器。

```bash
cd /home/shivice/code/reachy_mini/frontend
npm run dev
```

然后打开 Vite 输出的本地地址，通常是：

```text
http://localhost:5173/
```

如果 `5173` 被占用，Vite 会自动换到下一个端口，例如：

```text
http://localhost:5174/
```

开发模式下，Vite 会把 `/api` 代理到：

```text
http://127.0.0.1:8787
```

App Manager 是 Node 脚本，不需要额外安装 Python/FastAPI 服务依赖。

## 构建

```bash
cd /home/shivice/code/reachy_mini/frontend
npm run build
```

构建产物会输出到：

```text
frontend/dist/
```

## 应用描述文件

每个可启动的 app 根目录下都应该有一个 `reachy-app.json`。

示例：

```json
{
  "id": "my_app",
  "title": "My App",
  "description": "显示在切换器中的简短说明。",
  "environment": "shared",
  "python": "python",
  "venv": null,
  "module": "my_app.main",
  "args": [
    "--robot-host",
    "127.0.0.1"
  ],
  "frontendUrl": "http://127.0.0.1:8042/",
  "healthUrl": "http://127.0.0.1:8042/health",
  "stopSignal": "SIGINT",
  "setup": {
    "install": [
      "-e",
      "."
    ]
  },
  "setupHint": "首次运行前请在该 app 目录安装依赖。",
  "env": {}
}
```

字段说明：

- `id`：应用唯一 ID，前端和 API 都用它来启动/停止应用。
- `title`：前端展示名称。
- `description`：前端展示说明。
- `environment`：运行环境策略，支持 `shared` 和 `venv`。`shared` 表示使用启动 App Manager 时已激活的共享 Python/conda 环境；`venv` 表示为这个 app 使用独立虚拟环境。
- `python`：用于启动 app 或安装依赖的 Python 命令；共享 conda 环境通常写 `python`。
- `venv`：应用自己的虚拟环境目录。仅 `environment: "venv"` 时使用，推荐 `.venv`。
- `module`：启动模块。App Manager 会按环境策略展开为 `python -m <module> ...args` 或 `.venv/bin/python -m <module> ...args`。
- `args`：传给启动模块的参数。
- `command`：可选的完整启动命令。如果提供，会优先于 `module` 使用。
- `frontendUrl`：应用自己的页面地址。有值时，前端会用 iframe 内嵌，并提供新窗口打开按钮。
- `healthUrl`：预留的健康检查地址。
- `stopSignal`：停止应用时发送的信号，默认 `SIGINT`。
- `setup.install`：Setup 按钮会执行的 pip install 参数。上例会执行 `.venv/bin/python -m pip install -e .`。
- `setupHint`：前端展示的安装或运行提示。
- `env`：启动应用时附加的环境变量。

没有 `reachy-app.json` 的目录仍会出现在列表中，但 Start 按钮会禁用。

## 环境策略

当前推荐采用混合策略：

- 团队自研、常用、依赖可控的 app：优先使用共享主 conda 环境，descriptor 写 `environment: "shared"`，不创建单独 `.venv`。
- 第三方 demo、依赖重或版本敏感的 app：使用 `environment: "venv"` 和独立 `.venv`，避免影响主环境。

共享 conda 的关键点是：必须先在运行 App Manager 的终端里激活主环境，再执行：

```bash
npm run server
```

这样 App Manager 调用 `python` 和 `python -m pip install ...` 时，目标就是当前 conda 环境。

如果想确认“所有 app 共用一个大 conda 环境”是否安全，建议先创建临时环境验证：

```bash
conda create -n reachy_apps_test python=3.11
conda activate reachy_apps_test
python -m pip install -e "apps/clawbody[mediapipe_vision]"
python -m pip install -e apps/cookAIware
python -m pip install -e apps/reachy-dance-duo
python -m pip install -e apps/reachy_mirror
python -m pip install -e reachy_dialogue_app
python -m pip check
```

如果 `pip check` 没有报告冲突，再逐个启动 app 做最小测试；通过后就可以把这些依赖安装到正式主 conda 环境。不要直接在全局系统 Python 里安装。

## Setup 与 Start

为了避免污染系统 Python 环境，推荐至少使用一个明确的项目 conda 环境。必要时，每个 app 也可以使用自己的 `.venv`。

前端里的行为是：

```text
environment: "venv" 且没有 .venv
  -> 显示 Setup 按钮
  -> Start 按钮不可用

点击 Setup
  -> App Manager 执行 python3 -m venv .venv
  -> 升级 pip
  -> 按 setup.install 安装依赖
  -> 前端显示最近的安装日志

安装成功
  -> Start 按钮可用

environment: "shared"
  -> Start 默认可用
  -> Install deps 按钮会把 setup.install 安装到当前 conda 环境

安装失败
  -> app 状态显示 setup failed
  -> Start 按钮禁用，避免启动半安装环境
  -> 日志区域保留最近安装输出
  -> 可再次点击 Setup / Install deps 重试
```

Start 不会偷偷安装依赖。这样安装动作是显式的，失败时也能看到日志。

## 添加本地应用

可以直接把应用放进 `apps/`，也可以用软链接：

```bash
ln -s ../reachy_dialogue_app apps/reachy_dialogue_app
```

当前项目已经包含：

```text
apps/reachy_dialogue_app -> ../reachy_dialogue_app
reachy_dialogue_app/reachy-app.json
```

`apps` 下如果只是空目录，前端会展示它们，但无法启动。等应用代码下载或补齐后，
为每个应用添加 `reachy-app.json` 即可接入切换器。

## 当前已接入应用

目前 `apps/` 下这些应用已经有 `reachy-app.json`：

| 应用 | 前端地址 | 备注 |
| --- | --- | --- |
| `clawbody` | `http://127.0.0.1:7860/` | Gradio UI。首次运行前需要安装依赖并配置 OpenAI/OpenClaw 环境变量。 |
| `cookAIware` | `http://127.0.0.1:7860/` | ReachyMiniApp + Gradio/本地 UI。首次运行前需要安装依赖并配置 `OPENAI_API_KEY`。 |
| `reachy_dance_duo` | `http://127.0.0.1:9000/` | 本地 FastAPI/Web UI，使用 9000 端口。 |
| `reachy_mirror` | `http://127.0.0.1:7860/` | 摄像头动作镜像应用，使用共享 conda 环境，依赖 `mediapipe==0.10.14`。 |
| `reachy_dialogue_app` | `http://127.0.0.1:8042/` | 项目自研对话应用，通过软链接接入。 |

`clawbody`、`cookAIware` 和 `reachy_mirror` 都默认使用 7860 端口；App Manager 同一时间只运行一个 app，
所以正常切换时不会冲突。如果你手动在外部启动了其中一个，需要先停掉外部进程。

## 编写自研应用时的注意事项

- 应用应以前台进程运行，不要自己 daemonize。
- 收到 `SIGINT` 后应能干净退出。
- 机器人地址、后端地址、端口等配置尽量用命令行参数或环境变量传入。
- 如果应用有自己的 Web UI，请使用稳定端口，并写入 `frontendUrl`。
- 退出时释放机器人连接、麦克风、音频播放和子进程资源。
- 同一时间默认只运行一个 app；启动新 app 前，App Manager 会先停止当前 app。

## 接入官方生态应用

官方生态里的应用也可以通过 `reachy-app.json` 包装进来。App Manager 只关心：

1. 本地哪里能找到这个 app。
2. 用什么命令启动它。
3. 有没有应用自己的前端页面。

如果某个官方 app 仍然依赖官方 daemon app registry，可以写一个小启动脚本，把那部分逻辑包在脚本里。
前端仍然只需要调用本地 App Manager API。

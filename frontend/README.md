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
  "command": [
    "python3",
    "-m",
    "my_app.main",
    "--robot-host",
    "127.0.0.1"
  ],
  "frontendUrl": "http://127.0.0.1:8042/",
  "healthUrl": "http://127.0.0.1:8042/health",
  "stopSignal": "SIGINT",
  "setupCommand": [
    "python3",
    "-m",
    "pip",
    "install",
    "-e",
    "."
  ],
  "setupHint": "首次运行前请在该 app 目录安装依赖。",
  "env": {}
}
```

字段说明：

- `id`：应用唯一 ID，前端和 API 都用它来启动/停止应用。
- `title`：前端展示名称。
- `description`：前端展示说明。
- `command`：启动命令。App Manager 会在 app 目录中执行它。
- `frontendUrl`：应用自己的页面地址。有值时，前端会用 iframe 内嵌，并提供新窗口打开按钮。
- `healthUrl`：预留的健康检查地址。
- `stopSignal`：停止应用时发送的信号，默认 `SIGINT`。
- `setupCommand`：推荐的依赖安装命令。当前只展示提示，不会自动执行。
- `setupHint`：前端展示的安装或运行提示。
- `env`：启动应用时附加的环境变量。

没有 `reachy-app.json` 的目录仍会出现在列表中，但 Start 按钮会禁用。

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
| `reachy_dialogue_app` | `http://127.0.0.1:8042/` | 项目自研对话应用，通过软链接接入。 |

`clawbody` 和 `cookAIware` 都默认使用 7860 端口；App Manager 同一时间只运行一个 app，
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

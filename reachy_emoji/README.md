# Reachy Emoji 终端表情动画

这个模块会用 `curses` 在终端中渲染黑白字符帧，并通过 FastAPI 接口接收控制信号切换表情。运行时不再依赖 mpv，也不会在终端中实时播放 mp4。

## 功能概览

- 默认播放“静态”表情的进入姿势和可循环动作。
- 接收到控制信号后，当前表情播放回正，再切换到新表情。
- 控制信号可来自 JSON 请求或 URL 路径，且支持 emoji 多对一映射。
- 映射配置外置到 `config.json`，便于修改。
- 字符帧在终端中自动居中，终端 resize 后会在下一帧重新布局。
- `curses` 负责隐藏光标、批量刷新和退出时恢复终端状态。

## 依赖安装

### Python 依赖

运行时建议在已有的虚拟环境里安装：

```bash
pip install fastapi uvicorn pydantic
```

Linux 上的 Python 通常自带 `curses`。当前运行链路不需要 `python-mpv`、mpv 或 ffmpeg。

### 离线帧转换

`animations/` 已包含由当前 `videos/` 素材转换出的终端帧资产。只有在素材变化、想调整字符分辨率或阈值时，才需要离线转换工具：

```bash
python3 tools/convert_terminal_frames.py
```

该脚本会调用系统 `ffmpeg` 解码 mp4，并生成 `.tanim.json.gz` 文件；主程序不会调用它。默认从方形素材采样为 `48 x 48` 个黑白像素，再生成 `48 x 24` 个终端字符单元，单元内部使用 `▀`、`▄`、`█` 表达两行黑白像素。这个比例会补偿常见终端字符单元的纵向高度，让圆形不容易被横向拉宽。

常用调节参数：

```bash
python3 tools/convert_terminal_frames.py --width 64 --pixel-height 64 --fps 15 --threshold 48
```

## 运行方式

```bash
python main.py
```

默认会启动 FastAPI 服务（配置见 `config.json`）。渲染循环运行在启动它的终端中。通过 SSH 启动时请保持该终端连接；按 `Ctrl+C` 退出后 `curses` 会恢复光标和屏幕模式。

## FastAPI 调用方式

### 1) 通过信号映射（POST）

```bash
curl -X POST http://localhost:8001/signal \
  -H "Content-Type: application/json" \
  -d '{"signal":"angry"}'
```

### 2) 通过 URL 路径（GET）

```bash
# 直接使用 emoji
curl http://localhost:8001/😀

# 或使用 URL 编码
curl http://localhost:8001/%F0%9F%98%80
```

### 3) 直接指定表情（POST）

```bash
curl -X POST http://localhost:8001/emotion \
  -H "Content-Type: application/json" \
  -d '{"emotion":"愤怒"}'
```

## 配置说明（config.json）

- `server.host` / `server.port`: FastAPI 监听地址
- `default_emotion`: 启动时默认表情
- `static_variant`: 静态表情帧子目录（如 `右上看` / `左上看`）
- `signal_map`: 控制信号到表情的映射（支持 emoji 多对一）

示例：

```json
{
  "signal_map": {
    "😀": "兴奋",
    "😄": "兴奋",
    "angry": "愤怒"
  }
}
```

## 常见问题

- 请求成功但不切换：请检查 `signal_map` 是否包含对应信号。
- 提示找不到终端帧资产：确认 `animations/` 里存在对应表情的 `entry`、`loop`、`exit` `.tanim.json.gz` 文件，或重新运行离线转换脚本。
- 动画太小或太粗糙：用转换脚本提高 `--width`、`--pixel-height` 或 `--fps` 后重新生成帧资产。
- 动画只在日志里运行但终端没有正常显示：请在真实 tty、SSH 终端或常规终端启动，不要把主程序 stdout/stderr 直接接到不支持 curses 的展示目标。

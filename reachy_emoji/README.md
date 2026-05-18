# Reachy Emoji 终端表情播放

这个模块会用 mpv 的终端字符输出（tct）播放表情视频，并通过 FastAPI 接口接收控制信号切换表情。

## 功能概览

- 默认播放“静态”表情的进入姿势和可循环动作。
- 接收到控制信号后，当前表情播放回正，再切换到新表情。
- 控制信号可来自 JSON 请求或 URL 路径，且支持 emoji 多对一映射。
- 映射配置外置到 `config.json`，便于修改。

## 依赖安装

### Python 依赖

建议在已有的虚拟环境里安装：

```bash
pip install fastapi uvicorn pydantic python-mpv
```

### 系统依赖（mpv / libmpv）

需要系统层面的 mpv 或 libmpv：

```bash
# Ubuntu / Debian
sudo apt-get install mpv

# Fedora
sudo dnf install mpv

# Arch
sudo pacman -S mpv
```

## 运行方式

```bash
python main.py
```

默认会启动 FastAPI 服务（配置见 `config.json`）。

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
- `static_variant`: 静态表情子目录（如 `右上看` / `左上看`）
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
- 提示找不到视频文件：确认 `videos/` 目录下各表情的三段视频命名一致。
- 看不到终端输出：确认 mpv 已安装，并支持 `tct` 输出。

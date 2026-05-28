# Reachy Mini 手臂录制与融合调试 Pipeline

`action_pipeline/` 只负责动作录制、融合构建和调试；最终对外播放入口是 `action_call/`。

这套工具用于新的双臂动作生产流程：

1. 头部动作继续使用 `.run/arm_emotions_library` 里的 81 个官方 recorded moves。
2. 双臂动作在树莓派/Linux 上手动掰手臂录制成 `arm_clip JSON`。
3. 构建脚本按配置表把 arm clip 拉伸到每个头部动作时长，并生成调试动作库。
4. `play_action.py` 只用于 pipeline 内部调试；正式调用请使用 `action_call/play_emotion_action.py`。

## 目录

- `arm_clips/`：手动录制的双臂 clip，每个文件只包含 `time/left_arm/right_arm`。
- `config/signal_map.json`：外部信号、emoji、情绪词到 move name 的映射。
- `config/arm_clip_map.json`：需要你生成并填写的 81 个 head move 到 arm clip 的映射。
- `library/`：pipeline 调试输出目录，正式 82 动作库在 `action_call/library`。
- `record_arm_clip.py`：手动录制双臂 clip。
- `build_merged_library.py`：构建 81 个融合动作。
- `play_action.py`：调试播放融合动作。

## 前提

在树莓派上先启动 Reachy Mini daemon。当前机器录制默认使用 `disabled`，让手臂始终保持可手动掰动状态；不要使用 `gravity_compensation`。

如果 `python` 没有直接指向项目环境，可以使用当前机器上的 conda 环境：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python <command>
```

下面示例默认在项目根目录 `/home/lww/reachy_mini` 执行。

## 1. 录制手臂 clip

录制一个名为 `happy_wave` 的手臂动作：

```bash
python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "快乐挥手"
```

交互流程：

1. 脚本先切到 `disabled`，让手臂保持可手动掰动。
2. 把手臂放到初始姿态，按回车。
3. 按回车开始录制。
4. 手动掰动双臂。
5. 再按回车停止录制。
6. 文件写入 `action_pipeline/arm_clips/happy_wave.json`。

默认采样频率是 50 Hz。需要覆盖同名 clip 时加 `--overwrite`。

## 2. 生成 81 动作映射模板

```bash
python action_pipeline/build_merged_library.py --init-map-template
```

这会生成：

```text
action_pipeline/config/arm_clip_map.json
```

模板里有 81 个 move name，初始值是 `null`。你需要把每个值填成已录制的 `clip_id`，例如：

```json
{
  "schema_version": 1,
  "source_library": ".run/arm_emotions_library",
  "default_time_alignment": "stretch",
  "moves": {
    "cheerful1": "happy_wave",
    "sad1": "sad_low",
    "fear1": "fear_guard"
  }
}
```

必须显式配置所有 81 个动作。缺任何一个、写错 move name、或者引用不存在的 clip id，构建都会报错。

## 3. 构建融合库

```bash
python action_pipeline/build_merged_library.py
```

默认输入：

- 头部动作库：`.run/arm_emotions_library`
- 手臂 clips：`action_pipeline/arm_clips`
- 映射配置：`action_pipeline/config/arm_clip_map.json`

默认输出：

```text
action_pipeline/library/
```

构建规则固定为：

- 保留 source move 的 `description`、`time`、`head`、`body_yaw`、`check_collision`。
- 覆盖每帧 `left_arm` 和 `right_arm`。
- 把 arm clip 时间轴拉伸到 source move 的完整时长。
- 复制同名 `.wav` 到输出目录。

正式写文件前可以先校验：

```bash
python action_pipeline/build_merged_library.py --dry-run
```

## 4. 调试播放动作

本节只用于验证 `action_pipeline/library/` 中的中间构建结果。正式动作调用使用 `action_call/`。

按 signal/emoji 播放：

```bash
python action_pipeline/play_action.py --signal "😁"
```

直接按 move name 播放：

```bash
python action_pipeline/play_action.py --move cheerful1
```

查看当前信号映射和库状态：

```bash
python action_pipeline/play_action.py --list
```

默认只播放动作，不播放声音。需要声音时显式加：

```bash
python action_pipeline/play_action.py --signal "😁" --sound
```

播放后默认检查双臂是否回到逻辑零位；如果偏差超过 5°，会尝试强制回零。录制的动作如果设计为不回零，可以播放时加：

```bash
python action_pipeline/play_action.py --move cheerful1 --no-final-home-check
```

## 注意事项

- 这套 pipeline 不会修改 `hardware_config.yaml`，也不会重新写电机 offset。
- 如果电机重装后逻辑零位不对，需要先单独完成 offset/zero 校准。
- `action_call/` 是最终 82 动作调用入口；本目录保留录制、合成和调试 pipeline。
- 如果手臂掰不动，先执行 `curl -X POST http://localhost:8000/api/motors/set_mode/disabled`。

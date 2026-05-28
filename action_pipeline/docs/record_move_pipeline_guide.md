# Reachy Mini 手臂录制与融合调试流程

本文档说明 `action_pipeline/` 的使用流程：如何手动录制双臂动作，如何把录制出的手臂动作和官方头部 recorded move 库融合，以及如何做 pipeline 内部调试播放。

当前目录分工：

- `action_pipeline/`：录制手臂 clip、构建融合库、调试播放。
- `action_call/`：最终 82 动作播放入口，日常调用动作时使用这里。

## 1. 当前流程解决什么问题

官方 recorded move 库里已经有头部/身体动作。现在这套流程额外录制双臂动作，并把双臂轨迹按时间对齐到官方头部动作上，生成可用于调试或后续正式打包的“头部 + 身体 + 双臂”动作库。

整体数据流如下：

```text
官方头部动作库 .run/arm_emotions_library/*.json
        +
手动录制的双臂 clip action_pipeline/arm_clips/*.json
        +
81 个动作到 clip 的映射 action_pipeline/config/arm_clip_map.json
        ↓
action_pipeline/build_merged_library.py
        ↓
融合后的调试动作库 action_pipeline/library/*.json + *.wav
        ↓
action_pipeline/play_action.py 调试播放
        ↓
正式调用入口 action_call/
```

## 2. 重要目录和脚本

| 路径 | 作用 |
|---|---|
| `action_pipeline/record_arm_clip.py` | 手动掰动双臂并录制成 arm clip JSON。 |
| `action_pipeline/arm_clips/` | 保存录制好的双臂 clip。 |
| `action_pipeline/build_merged_library.py` | 把官方头部动作和 arm clip 融合成调试动作库。 |
| `action_pipeline/config/arm_clip_map.json` | 81 个官方动作名到 arm clip id 的映射表，需要手工填写。 |
| `action_pipeline/config/signal_map.json` | emoji/中文/英文信号到 move name 的映射表。 |
| `action_pipeline/library/` | pipeline 构建输出目录，用于调试检查。 |
| `action_pipeline/play_action.py` | 调试播放融合后的动作。 |
| `action_call/` | 最终 82 动作播放入口。 |
| `action_pipeline/README.md` | 代码恢复自带的简要说明。 |

## 3. 环境说明

当前 `lww` 用户下直接复用 `tzhx` 已配置好的 `robot` 环境。

真实环境路径是：

```bash
/home/tzhx/miniconda3/envs/robot
```

推荐使用方式：

```bash
cd /home/lww/reachy_mini
conda activate robot
```

如果 `conda activate robot` 在当前 shell 不可用，可以直接使用真实环境里的 Python：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python <command>
```

说明：

```text
/home/lww/miniforge3/envs/robot
```

只是指向 `/home/tzhx/miniconda3/envs/robot` 的软链接入口，不是一份独立复制出来的新环境。

## 4. 前置条件

### 4.1 需要启动 Reachy Mini daemon

录制和播放都需要连接本机正在运行的 Reachy Mini daemon。当前脚本默认连接本机：

```text
connection_mode = localhost_only
```

录制脚本默认不启用媒体：

```text
media_backend = no_media
```

播放脚本默认使用：

```text
media_backend = default
```

### 4.2 需要官方头部动作库

构建脚本默认读取：

```text
.run/arm_emotions_library
```

这个目录应该包含官方 81 个头部动作 JSON，通常还会包含同名 WAV。

当前如果本地还没有这个目录，先不要直接跑完整构建。你需要先把官方头部动作库准备到：

```bash
/home/lww/reachy_mini/.run/arm_emotions_library
```

快速检查：

```bash
cd /home/lww/reachy_mini
find .run/arm_emotions_library -maxdepth 1 -name '*.json' | wc -l
find .run/arm_emotions_library -maxdepth 1 -name '*.wav' | wc -l
```

理想情况下 JSON 数量应是 81。

## 5. 第一步：录制一个双臂 clip

进入仓库根目录：

```bash
cd /home/lww/reachy_mini
conda activate robot
```

录制一个名为 `happy_wave` 的动作：

```bash
python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "快乐挥手"
```

如果不用 `conda activate`，等价写法是：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "快乐挥手"
```

### 5.1 录制时的交互流程

脚本会按下面流程提示你：

1. 脚本先切换到 `disabled`，让手臂保持可手动掰动。
2. 把双臂放到初始姿态。
3. 按回车确认初始姿态。
4. 再按回车开始录制。
5. 手动掰动双臂完成动作。
6. 再按回车停止录制。
7. 脚本保持 `disabled`，方便继续录制。
8. 输出 clip 文件。

输出文件：

```text
action_pipeline/arm_clips/happy_wave.json
```

### 5.2 录制参数

常用参数：

```bash
python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "快乐挥手" \
  --sample-hz 50 \
  --motor-mode disabled
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--clip-id` | 必填 | 文件名和 clip id，只能用字母、数字、下划线、连字符。 |
| `--label` | 空 | 给人看的中文/英文说明。 |
| `--sample-hz` | `50.0` | 采样频率。 |
| `--motor-mode` | `disabled` | 手动录制模式。当前机器推荐固定使用 `disabled`，不要使用 `gravity_compensation`。 |
| `--overwrite` | false | 覆盖已有同名 clip。 |

如果同名文件已存在，需要加：

```bash
--overwrite
```

例如：

```bash
python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "快乐挥手" \
  --overwrite
```

## 6. 第二步：生成 81 动作映射模板

准备好官方头部动作库后，运行：

```bash
python action_pipeline/build_merged_library.py --init-map-template
```

如果不用 `conda activate`：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/build_merged_library.py --init-map-template
```

生成文件：

```text
action_pipeline/config/arm_clip_map.json
```

这个文件会包含官方头部库里的所有 move name，初始值通常是 `null`。

示例结构：

```json
{
  "schema_version": 1,
  "source_library": ".run/arm_emotions_library",
  "default_time_alignment": "stretch",
  "moves": {
    "cheerful1": null,
    "sad1": null,
    "fear1": null
  }
}
```

## 7. 第三步：填写动作到 clip 的映射

你需要编辑：

```text
action_pipeline/config/arm_clip_map.json
```

把每个官方动作名映射到一个已录制的 `clip_id`。

例如你录了这些手臂 clip：

```text
action_pipeline/arm_clips/happy_wave.json
action_pipeline/arm_clips/sad_low.json
action_pipeline/arm_clips/fear_guard.json
```

那么可以填写：

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

实际完整构建时，`moves` 里必须覆盖官方头部库中的所有动作。也就是说，如果源库有 81 个 JSON，就必须 81 个都填好。

构建脚本会严格检查：

- 是否缺少某个官方 move name。
- 是否多写了不存在的 move name。
- 是否有值为 `null`。
- 是否引用了不存在的 clip id。

## 8. 第四步：dry-run 校验

正式生成前先跑 dry-run：

```bash
python action_pipeline/build_merged_library.py --dry-run
```

如果不用 `conda activate`：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/build_merged_library.py --dry-run
```

成功时会列出每个官方动作使用哪个 clip，例如：

```text
cheerful1                <- happy_wave               frames= ... sound=yes
sad1                     <- sad_low                  frames= ... sound=yes
```

如果映射没填满或 clip 不存在，这一步会直接报错。先修 `arm_clip_map.json`，再继续。

## 9. 第五步：构建融合动作库

确认 dry-run 没问题后，正式构建：

```bash
python action_pipeline/build_merged_library.py
```

输出目录：

```text
action_pipeline/library/
```

构建结果：

- 每个官方动作生成一个同名 JSON。
- 如果源库有同名 WAV，会复制到输出目录。
- 生成的 JSON 可以被 `RecordedMoves` 加载。

### 9.1 融合规则

融合时会保留官方头部动作中的：

- `description`
- `time`
- `head`
- `body_yaw`
- `check_collision`

融合时会覆盖或补充每帧中的：

- `left_arm`
- `right_arm`

时间对齐方式：

```text
把 arm clip 的时间轴拉伸到官方头部动作的完整时长。
```

举例：

- 官方头部动作时长：3 秒
- 录制的手臂 clip 时长：1 秒
- 构建后，手臂动作会被拉伸到 3 秒，和头部动作一起播放

默认插值是线性插值：

```bash
--interpolation linear
```

也可以使用：

```bash
--interpolation minjerk
```

例如：

```bash
python action_pipeline/build_merged_library.py --interpolation minjerk
```

## 10. 第六步：查看 pipeline 调试库状态

构建完成后先查看：

```bash
python action_pipeline/play_action.py --list
```

它会显示：

- 当前 `signal_map.json` 中有哪些 emoji/中文/英文信号。
- 每个信号映射到哪个 move name。
- 对应 move 在 `action_pipeline/library/` 这个调试库中是否 ready。

示例：

```text
'😁' -> cheerful1 [ready]
'快乐' -> cheerful1 [ready]
'happy' -> cheerful1 [ready]
```

如果显示 `[missing]`，说明对应 move 还没有构建出来。

## 11. 第七步：pipeline 调试播放测试

这里验证的是 `action_pipeline/library/` 的中间结果。最终 82 动作调用以 `action_call/README.md` 为准。

### 11.1 按 move name 播放

推荐第一次测试直接用 move name：

```bash
python action_pipeline/play_action.py --move cheerful1
```

如果不用 `conda activate`：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/play_action.py --move cheerful1
```

### 11.2 按 signal / emoji 播放

当前默认映射在：

```text
action_pipeline/config/signal_map.json
```

比如：

```json
"😁": "cheerful1",
"快乐": "cheerful1",
"happy": "cheerful1"
```

播放：

```bash
python action_pipeline/play_action.py --signal "😁"
python action_pipeline/play_action.py --signal "快乐"
python action_pipeline/play_action.py --signal happy
```

### 11.3 播放声音

默认只播放动作，不播放声音。

如果要播放同名 WAV：

```bash
python action_pipeline/play_action.py --move cheerful1 --sound
```

### 11.4 关闭最终手臂回零检查

默认播放后会检查双臂是否回到逻辑零位。如果动作设计本来就不是回零，可以关闭：

```bash
python action_pipeline/play_action.py --move cheerful1 --no-final-home-check
```

## 12. 推荐第一次上手测试流程

### 12.1 最小完整流程

```bash
cd /home/lww/reachy_mini
conda activate robot

# 1. 确认官方头部库存在
find .run/arm_emotions_library -maxdepth 1 -name '*.json' | wc -l

# 2. 录一个手臂动作
python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "快乐挥手"

# 3. 生成 81 动作映射模板
python action_pipeline/build_merged_library.py --init-map-template

# 4. 编辑映射文件，把 cheerful1 等动作映射到 happy_wave
nano action_pipeline/config/arm_clip_map.json

# 5. 先 dry-run
python action_pipeline/build_merged_library.py --dry-run

# 6. 正式构建
python action_pipeline/build_merged_library.py

# 7. 查看状态
python action_pipeline/play_action.py --list

# 8. 播放测试
python action_pipeline/play_action.py --move cheerful1
```

### 12.2 如果你只想局部试一个 demo

当前构建器默认要求 source-dir 里的所有动作都映射完整。如果你想先只测一个动作，可以临时建一个只包含单个动作的 source 目录，然后指定 `--source-dir`。

示意：

```bash
mkdir -p /tmp/reachy_one_move_source
cp .run/arm_emotions_library/cheerful1.json /tmp/reachy_one_move_source/
cp .run/arm_emotions_library/cheerful1.wav /tmp/reachy_one_move_source/  # 如果有 wav
```

然后建一个只包含 `cheerful1` 的 map，例如 `/tmp/one_move_map.json`：

```json
{
  "schema_version": 1,
  "source_library": "one_move_test",
  "default_time_alignment": "stretch",
  "moves": {
    "cheerful1": "happy_wave"
  }
}
```

构建到临时输出：

```bash
python action_pipeline/build_merged_library.py \
  --source-dir /tmp/reachy_one_move_source \
  --map /tmp/one_move_map.json \
  --output-dir /tmp/reachy_one_move_library
```

播放临时库：

```bash
python action_pipeline/play_action.py \
  --library-dir /tmp/reachy_one_move_library \
  --move cheerful1
```

这适合第一次验证，不需要一次性填完 81 个映射。

## 13. 常见问题

### 13.1 `.run/arm_emotions_library` 不存在

现象：

```text
FileNotFoundError 或 source-dir 不存在
```

处理：

- 先准备官方 81 头部动作库到 `.run/arm_emotions_library`。
- 或者临时用 `--source-dir` 指向已有 recorded move 目录。

### 13.2 `disabled` 失败

录制默认使用：

```text
gravity_compensation
```

如果失败，优先检查：

- daemon 是否已经启动。
- 当前 daemon 是否支持 Placo kinematics。
- 机器人是否已正确连接。

可以临时尝试：

```bash
python action_pipeline/record_arm_clip.py \
  --clip-id test_disabled \
  --label "disabled test" \
  --motor-mode disabled
```

当前机器推荐固定使用 `disabled`。

### 13.3 构建时报缺少 mapping

说明 `action_pipeline/config/arm_clip_map.json` 没填完整。

处理：

- 确认 source-dir 里有多少 JSON。
- `moves` 里必须有同样数量的 key。
- 每个 key 的值都必须是已有 clip id。

### 13.4 播放时报 missing move

先运行：

```bash
python action_pipeline/play_action.py --list
```

如果对应 move 显示 `[missing]`，说明：

- 还没构建动作库；或
- `--library-dir` 指错了；或
- `signal_map.json` 映射到了一个不存在的 move name。

### 13.5 播放后双臂回零失败

默认播放后会检查手臂是否回到逻辑零位。失败可能说明：

- 动作本身最后没有回零。
- 机械零位/offset 不对。
- 电机跟踪异常。

如果动作设计上不需要回零，可以加：

```bash
--no-final-home-check
```

如果希望保留检查，就让录制动作最后回到逻辑零位附近。

## 14. 当前代码恢复验证结果

恢复后已验证：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python -B -m py_compile action_pipeline/*.py tests/unit_tests/test_action_pipeline.py
/home/tzhx/miniconda3/envs/robot/bin/python -B -m pytest -q tests/unit_tests/test_action_pipeline.py
```

结果：

```text
5 passed, 1 warning
```

无硬件的构建/插值逻辑也已验证通过。

## 15. 建议的实际录制策略

不要一开始就录 81 个完全不同的手臂动作。更稳妥的做法：

1. 先录 3 到 5 个基础 arm clip：
   - `happy_wave`
   - `sad_low`
   - `fear_guard`
   - `angry_strong`
   - `surprise_open`
2. 先把多个官方 head move 映射到这些基础 clip。
3. 构建并测试整体节奏是否合理。
4. 再逐步细化，为更多动作录制专用 clip。

这样能快速得到一个完整可播放的融合库，再逐步提升表现力。

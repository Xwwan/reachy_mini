# Reachy Mini 双臂魔改版二次开发说明

> 当前正式动作调用入口是 `action_call/`，其中包含 82 个动作：81 个官方头部动作融合手臂 clip，加上 `test_arm_002` 保留动作。
> 本文中较早的“五个情绪动作”说明是历史阶段记录，仅用于理解演进过程；新录制和融合流程以 `action_pipeline/` 与 `action_call/README.md` 为准。

本文档写给后续接手本项目的同学，用来快速理解：

- `reachy_mini` 这个项目本来负责什么。
- 本仓库的魔改版相对官方版本改了什么。
- 本地魔改 `rmmc` wheel 和 `reachy_mini` 如何配合。
- 拿到项目之后如何安装、编译、启动、验证。
- `action_call` 目录如何使用和继续开发。

本文档所有命令默认在 `reachy_mini` 项目根目录执行。推荐目录布局如下：

```text
workspace-root/
  reachy_mini/   <- 当前项目根目录
  rmmc/          <- 与 reachy_mini 同级的底层电机控制项目
```

因此，文档里用 `.` 表示当前 `reachy_mini` 根目录，用 `..\rmmc` 表示同级的 `rmmc` 项目。如果你的目录结构不同，只需要把 `..\rmmc` 改成你机器上的相对路径。

## 1. 项目分层：先搞清楚谁控制谁

这个机器人控制链路可以粗略理解成三层：

```text
应用/动作层
  action_call、examples、你写的情绪动作脚本

SDK/daemon 层
  reachy_mini

底层电机驱动层
  reachy_mini_motor_controller，也就是 rmmc 编译出来的 wheel
```

### 1.1 `reachy_mini` 是什么

`reachy_mini` 是官方 Reachy Mini 的 Python SDK 和 daemon 项目。它的核心作用不是直接“发串口字节”给电机，而是提供更高层的机器人控制接口。

它主要负责：

- 启动 `reachy-mini-daemon`，在电脑上跑一个本地服务。
- 连接真实机器人串口，例如 `COM3`。
- 暴露 Python SDK，例如 `ReachyMini()`。
- 提供 REST/WebSocket 接口，给桌面 App、网页、脚本调用。
- 管理头部运动学、动作插值、动作播放、音频、相机等功能。
- 从 recorded move JSON 中读取动作序列并播放。

简单说：`reachy_mini` 是“机器人 SDK + 服务端 + 高层动作调度器”。

### 1.2 `rmmc` 是什么

`rmmc` 是 `reachy_mini_motor_controller` 的源码项目。它主要负责和 Dynamixel 电机通信，是更底层的电机控制轮子。

官方版本的 `rmmc` 面向原始 Reachy Mini 结构：

- body rotation：1 个电机
- Stewart 平台：6 个电机
- 天线：2 个电机

本项目里的 `rmmc` 是魔改版：

- 删除了原来的左右天线概念。
- 增加了左右双臂。
- 每只手臂 2 个自由度。
- 电机 ID 当前约定为：
  - `left_arm_1`: 17
  - `left_arm_2`: 18
  - `right_arm_1`: 19
  - `right_arm_2`: 20

也就是说，魔改 `rmmc` wheel 的作用是让底层驱动认识“手臂电机”，而不是只认识“天线电机”。

### 1.3 为什么只改 `rmmc` 不够

只改 `rmmc`，底层确实可以让 17/18/19/20 这些电机动起来。但官方 `reachy_mini` 的上层接口、daemon、recorded move、状态消息等原本都假设机器人有两个天线，而不是两只双自由度手臂。

所以本仓库做的是两件事：

1. 底层 `rmmc` 改成双臂电机驱动。
2. 上层 `reachy_mini` 改成双臂 SDK/daemon/动作播放接口。

两者必须一起用，才能通过 `reachy_mini` 的高层接口播放“头部 + 双臂”的情绪动作。

## 2. 本仓库的魔改目标

本仓库当前目标不是继续兼容官方双天线机器人，而是服务于“去掉天线、增加双臂”的 Reachy Mini 魔改机型。

主要解决的问题：

- 官方 recorded move 只有头部、身体、天线动作。
- 当前硬件没有天线，改成了左右双臂。
- 每只手臂有两个电机，需要 `left_arm: [arm_1, arm_2]` 和 `right_arm: [arm_1, arm_2]` 这样的双自由度数据结构。
- 需要把情绪动作库从“头部 + 天线”改成“头部 + 双臂”。
- 需要让 daemon、SDK、REST/WebSocket、recorded move 播放链路都支持双臂。

当前 motion 数据统一使用：

```json
{
  "head": [[...], [...], [...], [...]],
  "body_yaw": 0.0,
  "check_collision": false,
  "left_arm": [0.0, 0.0],
  "right_arm": [0.0, 0.0]
}
```

其中：

- `head` 是 4x4 位姿矩阵，用来描述头部空间姿态。
- `body_yaw` 是身体旋转角度。
- `left_arm` 是左臂两个关节角度，单位 rad。
- `right_arm` 是右臂两个关节角度，单位 rad。

人调动作时通常用度数更直观，但 JSON 最终保存时使用 rad。

## 3. 重要目录说明

### 3.1 `src/reachy_mini`

SDK 和 daemon 主体代码。

重点关注：

```text
src/reachy_mini/reachy_mini.py
src/reachy_mini/motion/
src/reachy_mini/daemon/
src/reachy_mini/io/
src/reachy_mini/assets/config/hardware_config.yaml
```

常见开发位置：

- 想改 Python SDK 方法，看 `reachy_mini.py`。
- 想改 recorded move 格式和插值，看 `motion/recorded_move.py`。
- 想改 daemon 后端如何驱动真实电机，看 `daemon/backend/robot/backend.py`。
- 想改 REST API 请求/响应字段，看 `daemon/app/routers` 和 `daemon/app/models.py`。
- 想改 WebSocket 协议，看 `io/protocol.py`。
- 想改电机 ID、offset、限位，看 `assets/config/hardware_config.yaml`。

### 3.2 `examples`

开发和调试脚本。

比较重要的脚本：

```text
examples/tune_arm_sequence.py
examples/test_arm_action_presets.py
examples/debug_arm_offsets.py
examples/recorded_moves.py
```

用途：

- `tune_arm_sequence.py`：手动调一个左右对称的双臂动作。
- `test_arm_action_presets.py`：测试预设的 5 组双臂动作。
- `debug_arm_offsets.py`：检查 17/18/19/20 的 offset、当前位置、逻辑角度。
- `recorded_moves.py`：播放一个 recorded move 动作库。

### 3.3 `scripts`

数据转换脚本。

重点：

```text
scripts/apply_arm_motion_spec.py
scripts/convert_recorded_moves_to_arms.py
```

`apply_arm_motion_spec.py` 的作用是：

- 读取原始 recorded move JSON。
- 保留 `description`、`time`、`head`、`body_yaw`、`check_collision`。
- 只重写 `left_arm` 和 `right_arm`。
- 把人类可读的角度动作 spec 自动插值成每一帧的 rad 数值。

### 3.4 `action_call`

这是当前正式动作播放入口，负责按 `action_call/config.json` 调用 `action_call/library/` 里的 82 个动作。

`action_pipeline/` 负责录制、融合构建和调试；日常播放动作时优先看 `action_call/README.md`。

### 3.5 `env_exports`

环境导出和环境差异说明。

重点：

```text
env_exports/reach-mini-latest-vs-reachy-mini.md
env_exports/reach-mini-latest.full.no-prefix.yml
env_exports/reach-mini-latest.local-overrides.requirements.txt
```

如果别人要复现环境，优先看这个目录。

## 4. 硬件配置：`hardware_config.yaml`

当前双臂电机配置在：

```text
src/reachy_mini/assets/config/hardware_config.yaml
```

当前关键电机约定：

```text
left_arm_1   id 17
left_arm_2   id 18
right_arm_1  id 19
right_arm_2  id 20
```

当前这台机器上的校准值大致为：

```yaml
left_arm_1:
  id: 17
  offset: 78

left_arm_2:
  id: 18
  offset: 37

right_arm_1:
  id: 19
  offset: 0
  software_zero_offset_rad: -3.1048

right_arm_2:
  id: 20
  offset: 885
  software_zero_offset_rad: 0.0
```

注意：

- `offset` 是写入 Dynamixel 的 homing offset，不是动作角度。
- `software_zero_offset_rad` 是软件层零位补偿，用来处理某些电机机械安装导致的零位跨越问题。
- 动作脚本里发送的 `[0, 0]` 指的是“校准后的逻辑零位”，不是原始编码器位置。
- 换机器人、重装电机、重新装配手臂后，offset 可能需要重新标定。

检查 offset：

```bat
conda activate reach-mini-latest
python .\examples\debug_arm_offsets.py --serial COM3
```

写入单个电机配置：

```bat
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml left_arm_1 COM3 --update-config
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml left_arm_2 COM3 --update-config
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml right_arm_1 COM3 --update-config
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml right_arm_2 COM3 --update-config
```

## 5. 环境安装：推荐方式

当前推荐使用环境名：

```text
reach-mini-latest
```

如果从零创建：

```bat
conda env create -f .\env_exports\reach-mini-latest.full.no-prefix.yml
conda activate reach-mini-latest
conda env config vars set PYTHONNOUSERSITE=1
conda deactivate
conda activate reach-mini-latest
```

然后安装本地 `reachy_mini` 源码。以下命令在 `reachy_mini` 根目录执行：

```bat
python -m pip install -e .
```

再安装本地魔改 `rmmc` wheel：

```bat
python -m pip install --force-reinstall ..\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

验证：

```bat
python -m pip show reachy-mini reachy-mini-motor-controller
python -c "import reachy_mini, reachy_mini_motor_controller; print(reachy_mini.__file__); print(reachy_mini_motor_controller.__file__)"
```

期望结果：

- `reachy_mini` 指向本地源码 `.\src\reachy_mini`。
- `reachy_mini_motor_controller` 是从本地 wheel 安装的魔改版本。

## 6. 如果别人已经有官方 `reachy-mini` 环境

如果别人已经安装了官方环境，也已经拿到了本地魔改 wheel，可以直接更新官方环境。以下命令在 `reachy_mini` 根目录执行：

```bat
conda activate reachy-mini
conda env config vars set PYTHONNOUSERSITE=1
conda deactivate
conda activate reachy-mini

python -m pip install -e .
python -m pip install --force-reinstall ..\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

如果还要编译 `rmmc`，再补：

```bat
python -m pip install maturin
```

说明：

- 官方环境里的 `opencv-python` 可以保留，不影响双臂控制。
- 最关键的是 `pip install -e .` 和 `--force-reinstall` 本地魔改 wheel。
- 如果不安装本地 wheel，`reachy_mini` 可能会调用到官方电机控制器，双臂接口会不匹配。

## 7. 编译并安装魔改 `rmmc` wheel

进入 `rmmc` 项目：

```bat
conda activate reach-mini-latest
cd /d ..\rmmc
python -m pip install maturin
```

本项目的 `rmmc` 使用 Rust + Python binding，构建工具是 `maturin`。

### 7.1 开发安装

```bat
python -m pip install -e . --verbose
```

这会在当前环境中以 editable/development 方式构建安装，适合本地调试。

### 7.2 构建 wheel

如果需要明确产出 wheel，推荐：

```bat
maturin build --release
```

生成文件通常在 `rmmc` 项目内的：

```text
.\target\wheels\
```

例如：

```text
reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

### 7.3 安装 wheel 到 `reachy_mini` 环境

回到 `reachy_mini` 根目录后执行：

```bat
conda activate reach-mini-latest
python -m pip install --force-reinstall ..\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

为什么要 `--force-reinstall`：

- 这个 wheel 包名和官方包一样，都是 `reachy_mini_motor_controller`。
- 版本号也可能一样，例如 `1.5.5`。
- 不强制重装时，pip 可能认为已经安装过同版本，于是不替换成本地魔改版。

### 7.4 编译失败常见原因

1. 没装 Rust 工具链。

   需要安装 Rust，确认：

   ```bat
   rustc --version
   cargo --version
   ```

2. Python 版本不匹配。

   当前 wheel 是 `cp312`，对应 Python 3.12。

3. 没在目标 conda 环境里编译。

   先确认：

   ```bat
   where python
   python --version
   ```

4. wheel 路径写错。

   用下面命令确认：

   ```bat
   dir ..\rmmc\target\wheels
   ```

## 8. 启动机器人 daemon

机器人上电，USB 接电脑后，先确认串口，例如 `COM3`。

启动 daemon。以下命令在 `reachy_mini` 根目录执行：

```bat
conda activate reach-mini-latest
reachy-mini-daemon --serialport COM3
```

这个终端保持打开。

如果提示 8000 端口被占用，说明已有 daemon 在跑。可以先关闭旧终端，或者在任务管理器里结束旧的 Python 进程。

如果提示找不到电机：

- 检查机器人电源。
- 检查 USB 串口。
- 检查是否是正确 COM 口。
- 检查电机线是否接好。

## 9. 双臂调试流程

### 9.1 先看当前 offset 和逻辑角度

```bat
python .\examples\debug_arm_offsets.py --serial COM3
```

目标是看到 17/18/19/20 都在线，并且逻辑角度接近 0 度。

### 9.2 测试单组动作

daemon 已经启动后，另开一个终端，并在 `reachy_mini` 根目录执行：

```bat
conda activate reach-mini-latest
python .\examples\tune_arm_sequence.py --main-deg -60 --swing-deg 60 --repeats 2 --max-abs-deg 100
```

动作逻辑：

1. 先回到逻辑零位。
2. 17/19 主关节先转到主角度。
3. 18/20 第二关节做来回摆动。
4. 18/20 回正。
5. 17/19 回正。
6. 最终检查是否回到零位。

### 9.3 测试旧版预设动作

```bat
python .\examples\test_arm_action_presets.py --preset 1
python .\examples\test_arm_action_presets.py --preset 2
python .\examples\test_arm_action_presets.py --preset 3
python .\examples\test_arm_action_presets.py --preset 4
python .\examples\test_arm_action_presets.py --preset 5
```

只打印不运动：

```bat
python .\examples\test_arm_action_presets.py --all --dry-run
```

## 10. `action_call`：最终 82 动作播放入口

`action_call` 是最终对外调用动作的应用层目录。当前版本包含 82 个动作：81 个官方头部动作融合手臂 clip，加上保留动作 `test_arm_002`。

路径：

```text
.\action_call
```

目录结构：

```text
action_call
  build_action_library.py
  play_emotion_action.py
  README.md
  config.json
  record_20_arm_actions.md
  library
```

### 10.1 `library`

最终播放用的 JSON 和 wav。每个 JSON 是一个可由 `RecordedMoves` 加载的完整动作。

```text
action_call/library/amazed1.json
action_call/library/cheerful1.json
action_call/library/test_arm_002.json
```

这些 JSON 已经把原始头部动作和双臂动作合并好了。

保留内容：

- 原始 `description`
- 原始 `time`
- 原始 `head`
- 原始 `body_yaw`
- 原始 `check_collision`

重写内容：

- `left_arm`
- `right_arm`

### 10.2 `config.json`

这里保存动作调用映射。命令行传入的 `--signal` 会在 `signal_map` 里映射到具体 move name。

```text
action_call/config.json
```

不要直接手改 `action_call/library/*.json`。如果要调整手臂动作，应该回到 `action_pipeline/arm_clips/` 重新录制，或者重新运行构建脚本。

### 10.3 `build_action_library.py`

作用：重新生成 `action_call/library`。以下命令在 `reachy_mini` 根目录执行：

命令：

```bat
conda activate reach-mini-latest
python .\action_call\build_action_library.py
```

它会从：

```text
.run/arm_emotions_library
action_pipeline/arm_clips
```

读取官方头部动作和已录制手臂 clip，再生成：

```text
action_call/library
```

当前 81 个官方动作到 20 个手臂 clip 的对应关系在 `action_call/build_action_library.py` 中维护。

### 10.4 `play_emotion_action.py`

作用：连接已经启动的 daemon，按 `--signal` 播放指定动作。

先列出可用情绪：

```bat
python .\action_call\play_emotion_action.py --list
```

播放：

```bat
python .\action_call\play_emotion_action.py --signal cheerful
python .\action_call\play_emotion_action.py --signal sad
python .\action_call\play_emotion_action.py --signal fear
python .\action_call\play_emotion_action.py --signal furious
python .\action_call\play_emotion_action.py --signal surprised
```

不播放声音，只播放动作：

```bat
python .\action_call\play_emotion_action.py --signal cheerful --no-sound
```

重复播放：

```bat
python .\action_call\play_emotion_action.py --signal cheerful --repeat 3
```

关闭最终回正检查：

```bat
python .\action_call\play_emotion_action.py --signal cheerful --no-final-home-check
```

默认情况下，脚本会在动作结束后检查双臂是否回到逻辑零位。如果偏差超过阈值，会自动强制复位。

### 10.5 如何调整或新增动作

推荐流程：

1. 在 `action_pipeline/record_arm_clip.py` 中重新录制或新增手臂 clip。
2. 如果只是新增 signal 名称，修改 `action_call/config.json`。
3. 如果要改变官方动作和手臂 clip 的融合关系，修改 `action_call/build_action_library.py`。
4. 运行：

   ```bat
   python .\action_call\build_action_library.py
   ```

5. 运行：

   ```bat
   python .\action_call\play_emotion_action.py --list
   ```

6. 启动 daemon 后实际播放测试。

不要直接手改 `action_call/library/*.json`，因为那里是生成产物。应该改生成逻辑或 spec，再重新生成。

## 11. recorded move JSON 的理解

一个 recorded move JSON 大致长这样：

```json
{
  "description": "...",
  "time": [0.0, 0.01, 0.02],
  "set_target_data": [
    {
      "head": [[...], [...], [...], [...]],
      "body_yaw": 0.0,
      "check_collision": false,
      "left_arm": [0.0, 0.0],
      "right_arm": [0.0, 0.0]
    }
  ]
}
```

解释：

- `time` 是每一帧的时间戳。
- `set_target_data` 和 `time` 一一对应。
- `head` 是头部目标位姿。
- `left_arm/right_arm` 是对应时间点的手臂目标角度，单位 rad。

本项目采用的策略是：

- 头部动作沿用官方情绪库。
- 双臂动作由我们自己设计。
- 使用插值把少量关键帧变成完整时间轴上的逐帧手臂目标。

这样做的好处是：

- 不需要手动给每个时间戳写一组手臂角度。
- 头部动作和声音仍然保留官方素材。
- 双臂动作可以独立调参。

## 12. 二次开发建议

### 12.1 只想改情绪动作

优先改：

```text
action_call/build_action_library.py
action_call/config.json
action_pipeline/record_arm_clip.py
action_pipeline/arm_clips
```

通常不需要碰 daemon 和底层 `rmmc`。

### 12.2 想新增手臂控制 API

看：

```text
src/reachy_mini/reachy_mini.py
src/reachy_mini/daemon/app/routers/move.py
src/reachy_mini/daemon/app/models.py
src/reachy_mini/io/protocol.py
```

### 12.3 想改真实电机控制方式

看：

```text
src/reachy_mini/daemon/backend/robot/backend.py
..\rmmc\src
```

如果需要底层新增接口，通常要：

1. 改 `rmmc` Rust 代码。
2. 重新编译 wheel。
3. 在目标 conda 环境中 `--force-reinstall` wheel。
4. 改 `reachy_mini` backend 调用新接口。

### 12.4 想改仿真外观

当前第一版没有完整重做 URDF/MJCF 里的双臂外观。仓库里仍可能看到 `antenna` 相关 mesh 或 URDF 名称。

如果只是控制真实机器人和播放情绪动作，不需要优先改仿真外观。

如果要做可视化或仿真，需要额外改：

```text
src/reachy_mini/descriptions
src/reachy_mini/kinematics
```

这部分不是当前情绪动作链路的核心。

## 13. 常用命令速查

### 安装本地 `reachy_mini`

在 `reachy_mini` 根目录执行：

```bat
conda activate reach-mini-latest
python -m pip install -e .
```

### 编译 `rmmc` wheel

```bat
conda activate reach-mini-latest
cd /d ..\rmmc
python -m pip install maturin
maturin build --release
```

### 安装 `rmmc` wheel

```bat
python -m pip install --force-reinstall ..\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

### 启动 daemon

在 `reachy_mini` 根目录执行：

```bat
conda activate reach-mini-latest
reachy-mini-daemon --serialport COM3
```

### 播放情绪动作

```bat
python .\action_call\play_emotion_action.py --signal cheerful
```

### 重新生成 82 动作库

```bat
python .\action_call\build_action_library.py
```

### 检查动作库

```bat
python .\action_call\play_emotion_action.py --list
```

### 检查手臂 offset

```bat
python .\examples\debug_arm_offsets.py --serial COM3
```

## 14. 交接时最容易踩的坑

### 14.1 只装了官方 `reachy_mini_motor_controller`

现象：

- Python 能 import。
- daemon 也可能能启动。
- 但双臂接口或 17/18/19/20 控制不对。

解决：

```bat
python -m pip install --force-reinstall ..\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

### 14.2 忘了 `pip install -e .`

现象：

- 改了源码但运行没变化。
- 调用到的可能不是当前工作区代码。

解决：

在 `reachy_mini` 根目录执行：

```bat
python -m pip install -e .
```

### 14.3 PowerShell 里设置环境变量写法不对

CMD/Anaconda Prompt：

```bat
set PYTHONNOUSERSITE=1
```

PowerShell：

```powershell
$env:PYTHONNOUSERSITE = "1"
```

更推荐永久写进 conda 环境：

```bat
conda env config vars set PYTHONNOUSERSITE=1
conda deactivate
conda activate reach-mini-latest
```

### 14.4 端口 8000 被占用

说明已有 daemon 在跑。关闭旧 daemon 终端，或结束旧 Python 进程。

### 14.5 COM 口不对或没上电

如果 daemon 输出 `No motors detected` 或某些电机 not found，先检查：

- 电源是否打开。
- USB 是否连接。
- COM 口是否正确。
- 电机接线是否松动。

## 15. 推荐开发顺序

如果你是新接手的同学，建议按这个顺序来：

1. 先读本文档。
2. 创建或激活 `reach-mini-latest` 环境。
3. 安装本地 `reachy_mini`：`python -m pip install -e .`。
4. 编译并安装本地魔改 `rmmc` wheel。
5. 不接机器人时先运行：

   ```bat
   python .\action_call\play_emotion_action.py --list
   ```

6. 接机器人后启动 daemon：

   ```bat
   reachy-mini-daemon --serialport COM3
   ```

7. 先小幅测试双臂：

   ```bat
   python .\examples\test_arm_action_presets.py --preset 1
   ```

8. 再播放情绪动作：

   ```bat
   python .\action_call\play_emotion_action.py --signal cheerful
   ```

9. 如果要新增情绪，优先改 `action_call/build_action_library.py`，不要直接手改生成后的逐帧 JSON。

## 16. 一句话总结

本仓库的 `reachy_mini` 是官方 SDK/daemon 的双臂适配版；本地魔改 `rmmc` wheel 是底层电机控制适配版。前者负责“怎么组织和播放动作”，后者负责“怎么让 17/18/19/20 这些手臂电机动起来”。`action_pipeline` 负责录制和融合调试，`action_call` 负责最终 82 个动作的调用播放。

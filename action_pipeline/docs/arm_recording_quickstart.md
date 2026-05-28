# Reachy Mini 手臂动作录制快速流程

本文只说明怎么启动 daemon、怎么进入可手掰状态、怎么录制一个手臂 clip，以及录制后怎么检查输出文件。

目录分工：`action_pipeline/` 负责录制和调试；最终动作播放入口是 `action_call/`。

以下命令默认在树莓派 SSH 终端中执行，项目路径为：

```bash
/home/lww/reachy_mini
```

机器人串口默认是：

```bash
/dev/ttyACM0
```

## 0. 准备

确认机器人已经接好电源，USB/串口已连接到树莓派。

如果当前用户还没有永久串口权限，每次重插 USB 或重启后先执行：

```bash
sudo chmod 666 /dev/ttyACM0
```

确认串口存在：

```bash
ls -l /dev/ttyACM0
```

## 1. 启动 daemon

打开第一个 SSH 终端，执行：

```bash
cd /home/lww/reachy_mini
./start_daemon_lww.sh
```

启动时应看到类似输出：

```text
package: /home/lww/reachy_mini/src/reachy_mini/__init__.py
config : /home/lww/reachy_mini/src/reachy_mini/assets/config/hardware_config.yaml
```

并且最后应看到：

```text
Daemon started successfully.
```

录制手臂动作时不要使用：

```bash
WAKE_ON_START=1 ./start_daemon_lww.sh
```

原因是唤醒后电机会进入位置控制，手臂会比较难手动掰动。

保持第一个终端里的 daemon 不要关闭。

## 2. 打开第二个 SSH 终端

后续录制命令都在第二个 SSH 终端执行。

进入项目目录：

```bash
cd /home/lww/reachy_mini
```

可选：确认 daemon 正常运行：

```bash
curl http://localhost:8000/api/daemon/status
```

可选：确认当前电机模式：

```bash
curl http://localhost:8000/api/motors/status
```

## 3. 进入适合手掰的模式

本项目录制时固定推荐使用完全失能模式。不要使用 `gravity_compensation`，因为当前机器上该模式会让手臂难以手动掰动。

切到完全失能模式：

```bash
curl -X POST http://localhost:8000/api/motors/set_mode/disabled
```

检查：

```bash
curl http://localhost:8000/api/motors/status
```

期望看到：

```json
{"mode":"disabled"}
```

保持这个状态即可开始录制。

## 4. 录制一个手臂 clip

推荐命令：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id test_arm_001 \
  --label "测试手臂动作"
```

参数说明：

```text
--clip-id  输出文件名和动作 ID，只能用字母、数字、下划线、连字符
--label    给自己看的动作说明，可以写中文
```

如果同名 clip 已经存在，需要覆盖，添加 `--overwrite`：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id test_arm_001 \
  --label "测试手臂动作" \
  --overwrite
```

录制脚本现在默认就是 `disabled`，正常不需要额外传 `--motor-mode disabled`。如果想写得更明确，也可以这样执行：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id test_arm_001 \
  --label "测试手臂动作" \
  --motor-mode disabled \
  --overwrite
```

## 5. 录制时怎么操作

执行录制命令后，按终端提示操作。

脚本连接 daemon 后会先输出：

```text
Switching arms to disabled mode.
```

看到这行后，手臂应处于可手动掰动状态。

### 第一次回车

终端提示：

```text
Place the arms in the initial pose, then press Enter.
```

操作：

1. 用手把双臂摆到动作起始姿态。
2. 摆好后按一次回车。

### 第二次回车

终端提示：

```text
Press Enter to START recording.
```

操作：

1. 准备开始动作。
2. 按一次回车。
3. 按下后立即开始录制。

### 录制动作

终端提示：

```text
Recording. Move the arms by hand, then press Enter to STOP.
```

操作：

1. 手动掰动双臂，完成你要录的动作。
2. 动作做完后按一次回车停止录制。

### 录制结束

录制成功后会看到类似：

```text
Wrote /home/lww/reachy_mini/action_pipeline/arm_clips/test_arm_001.json with xxx samples over x.xxxs.
```

输出文件位置：

```bash
/home/lww/reachy_mini/action_pipeline/arm_clips/test_arm_001.json
```

## 6. 检查录制文件

查看文件是否生成：

```bash
ls -lh action_pipeline/arm_clips/test_arm_001.json
```

快速查看前几行：

```bash
head -40 action_pipeline/arm_clips/test_arm_001.json
```

统计当前已经录制了多少个 clip：

```bash
find action_pipeline/arm_clips -maxdepth 1 -name '*.json' | wc -l
```

列出所有 clip：

```bash
find action_pipeline/arm_clips -maxdepth 1 -name '*.json' | sort
```

## 7. 继续录制多个动作

每录一个新动作，换一个 `--clip-id`。

示例：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id happy_wave \
  --label "开心挥手"
```

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id sad_low \
  --label "难过低垂"
```

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id fear_guard \
  --label "害怕防御"
```

建议命名规则：

```text
情绪_动作
```

例如：

```text
happy_wave
happy_open
sad_low
sad_cover
fear_guard
angry_push
surprise_raise
```

## 8. 录制完成后恢复普通控制模式

如果后面要播放动作测试，需要把电机切回 enabled：

```bash
curl -X POST http://localhost:8000/api/motors/set_mode/enabled
```

确认：

```bash
curl http://localhost:8000/api/motors/status
```

期望看到：

```json
{"mode":"enabled"}
```

## 9. 停止 daemon

回到第一个 SSH 终端，也就是运行 daemon 的终端，按：

```text
Ctrl+C
```

等待它正常退出。

## 10. 常见情况

### daemon 启动时报 Permission denied

执行：

```bash
sudo chmod 666 /dev/ttyACM0
```

然后重新启动 daemon：

```bash
cd /home/lww/reachy_mini
./start_daemon_lww.sh
```

### 手臂很硬，掰不动

直接切 disabled：

```bash
curl -X POST http://localhost:8000/api/motors/set_mode/disabled
```

确认：

```bash
curl http://localhost:8000/api/motors/status
```

期望看到：

```json
{"mode":"disabled"}
```

然后重新运行录制命令。

### 录制文件已存在

加：

```bash
--overwrite
```

### 不确定 daemon 是否用了正确 YAML

启动 daemon 时必须看到：

```text
package: /home/lww/reachy_mini/src/reachy_mini/__init__.py
config : /home/lww/reachy_mini/src/reachy_mini/assets/config/hardware_config.yaml
```

如果显示 `/home/tzhx/code/...`，不要继续录制。

## 11. 最短可复制流程

第一个 SSH 终端：

```bash
sudo chmod 666 /dev/ttyACM0
cd /home/lww/reachy_mini
./start_daemon_lww.sh
```

第二个 SSH 终端：

```bash
cd /home/lww/reachy_mini

curl -X POST http://localhost:8000/api/motors/set_mode/disabled

/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id test_arm_001 \
  --label "测试手臂动作"
```

录完后，如果要继续录第二个：

```bash
/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py \
  --clip-id test_arm_002 \
  --label "第二个测试手臂动作"
```

录完后，如果要播放测试：

```bash
curl -X POST http://localhost:8000/api/motors/set_mode/enabled
```

正式调用融合后的 82 个动作时，看 `action_call/README.md`。

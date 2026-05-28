# Action Call

`action_call/` 是 82 个融合动作的调用入口：81 个官方头部动作融合手臂动作，加上保留动作 `test_arm_002`。

## 1. 启动 daemon

在第一个终端启动 Reachy Mini daemon。合并到 `tzhx` 目录后，推荐这样启动：

```bash
cd /home/tzhx/code/reachy_mini
/home/tzhx/miniconda3/envs/robot/bin/reachy-mini-daemon \
  --serialport auto \
  --kinematics-engine Placo
```

保持这个终端不要关闭。

## 2. 播放动作

在第二个终端调用播放脚本：

```bash
cd /home/tzhx/code/reachy_mini
/home/tzhx/miniconda3/envs/robot/bin/python action_call/play_emotion_action.py --signal amazed
```

查看所有可用 `--signal`：

```bash
cd /home/tzhx/code/reachy_mini
/home/tzhx/miniconda3/envs/robot/bin/python action_call/play_emotion_action.py --list
```


## 3. 配置文件

动作映射配置文件：

```text
action_call/config.json
```

动作文件目录：

```text
action_call/library
```

## 4. 目录分工

- `action_call/`：最终动作播放入口，负责按 `config.json` 映射调用 `library/` 里的 82 个动作。
- `action_pipeline/`：动作录制、动作融合构建和调试工具目录，不作为最终对外播放入口。

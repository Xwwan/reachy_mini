# 双臂五情绪动作调用

这个目录是一套独立的动作调用入口

## 目录内容

- `library/`：最终播放用的 JSON，保留原始头部动作，只替换双臂动作。
- `arm_motion_specs/`：人类可读的双臂角度动作，单位是度。
- `build_action_library.py`：从 `.run/arm_emotions_library` 重新生成 `library/`。
- `play_emotion_action.py`：连接已经启动的 daemon，播放指定情绪。

## 五个情绪和动作

| 调用名 | 中文 | 源动作 | 双臂动作 |
| --- | --- | --- | --- |
| `cheerful` | 快乐 | `cheerful1` | 主关节 left +30°, right -30°；第二关节 left ±45°, right ∓45°；重复 2 次 |
| `sad` | 悲伤 | `sad1` | 主关节 left -30°, right +30°；第二关节 left ±30°, right ∓30°；重复 3 次 |
| `fear` | 恐惧 | `fear1` | 主关节 left -60°, right +60°；第二关节 left ±45°, right ∓45°；重复 4 次 |
| `furious` | 愤怒 | `furious1` | 主关节 left -60°, right +60°；第二关节 left ±60°, right ∓60°；重复 3 次 |
| `surprised` | 惊讶 | `surprised1` | 主关节 left -60°, right +60°；第二关节 left ±45°, right ∓45°；重复 3 次 |


## 启动 daemon

机器人上电、USB 连接到电脑之后，在一个终端里运行：

```powershell
conda activate reach-mini-latest
cd E:\workspace\lab\reachy_mini
reachy-mini-daemon --serialport COM3
```

这个终端保持开着，不要关闭。
注：
reach-mini-latest环境构建方式
```powershell
cd /d E:\workspace\lab\reachy_mini
python -m pip install -e .
python -m pip install --force-reinstall XXX.whl（谭师兄魔改rmmc后导出的的wheel）
```

## 播放情绪

另开一个终端：

```powershell
conda activate reach-mini-latest
cd E:\workspace\lab\reachy_mini
python .\action_call\play_emotion_action.py --list
```

播放单个情绪：

```powershell
python .\action_call\play_emotion_action.py --emotion cheerful
python .\action_call\play_emotion_action.py --emotion sad
python .\action_call\play_emotion_action.py --emotion fear
python .\action_call\play_emotion_action.py --emotion furious
python .\action_call\play_emotion_action.py --emotion surprised
```

只播放动作、不播放声音：

```powershell
python .\action_call\play_emotion_action.py --emotion cheerful --no-sound
```

脚本默认会在动作结束后检查双臂是否回到逻辑零位。如果偏差超过 5°，会自动强制复位。


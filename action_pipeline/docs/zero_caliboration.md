# 使用 USB 校准双臂零点

本文记录在不重新安装电机或舵盘的情况下，通过 USB、DYNAMIXEL Wizard 和
`hardware_config.yaml` 为本项目的双臂设置逻辑零点的完整流程。

本文适用于当前分支中增加的四个手臂电机：

| 名称 | ID |
| --- | ---: |
| `left_arm_1` | 17 |
| `left_arm_2` | 18 |
| `right_arm_1` | 19 |
| `right_arm_2` | 20 |

## 原理

需要区分三种数值：

| 数值 | 含义 | 保存位置 |
| --- | --- | --- |
| `Present Position` | Wizard 中当前读取到的电机位置 | 电机运行反馈 |
| `offset` | DYNAMIXEL 的 `Homing Offset(20)` | 电机 EEPROM |
| `software_zero_offset_rad` | SDK 逻辑零点到电机目标角度的额外映射 | YAML，daemon 运行时使用 |

`offset` 不是“启动时要去的位置”。执行 `setup_motor` 时，配置里的
`offset` 会被写入电机的 `Homing Offset(20)` 寄存器。

对于 DYNAMIXEL XL330，在 Position Control Mode，即本项目使用的
`operating_mode: 3` 中：

- `Homing Offset` 的有效范围是 `-1024 .. 1024` pulse。
- `Min Position Limit` 和 `Max Position Limit` 必须落在 `0 .. 4095`。
- 电机反馈满足：

```text
Present Position = Actual Position + Homing Offset
```

daemon 中已经实现了双臂的软件零点映射：

```text
电机目标角度 = SDK 逻辑目标角度 + software_zero_offset_rad
```

启动时 `wake_up()` 会将四个手臂的 SDK 逻辑目标设置为 `0 rad`。因此，机械
安装无法调整时，正确做法是：

1. 使用合法的硬件 `offset` 将物理活动范围移入 `0 .. 4095`。
2. 使用 `software_zero_offset_rad` 让期望物理初始姿态对应 SDK 的 `0 rad`。

## 安全准备

校准涉及重新启动力矩。位置或符号填写错误时，电机可能突然冲向限位。

1. 将机器人放稳，并用手托住可能突然运动的手臂。
2. 使用电源适配器为电机供电；USB 线仅用于通信。
3. 同一时间只允许一个程序占用串口：Wizard、daemon 和 Python 烧写脚本不能同时连接 `COM3`。
4. 在 Wizard 中测量和修改 EEPROM 时，保持 `Torque Enable(64) = 0`。
5. 第一次启动 daemon 时使用 `--no-wake-up-on-start`，先检查配置是否正确加载。

## 所需文件与工具

本流程操作的配置文件是：

```text
E:\workspace\lab\reachy_mini\src\reachy_mini\assets\config\hardware_config.yaml
```

需要：

- Reachy Mini 与电脑之间的 USB 数据线。
- 电机供电。
- [DYNAMIXEL Wizard 2.0](https://emanual.robotis.com/docs/en/software/dynamixel/dynamixel_wizard2/)。
- 已安装本项目的 Python 环境，例如 `reach-mini-latest`。

以下命令以 Windows PowerShell 和 `COM3` 为例。

## 1. 确认运行的是本项目

daemon 启动时会检查并可能按当前导入的 `reachy_mini` 包内置 YAML 重新烧写
电机。必须保证导入的是正在编辑的项目，而不是另一个安装副本。

```powershell
conda activate reach-mini-latest
cd E:\workspace\lab\reachy_mini

python -c "import reachy_mini; from importlib.resources import files; print(reachy_mini.__file__); print(files(reachy_mini).joinpath('assets/config/hardware_config.yaml'))"
```

输出应指向 `E:\workspace\lab\reachy_mini\src\reachy_mini\...`。如果不是，
在该环境中安装当前源码：

```powershell
python -m pip install -e .
```

再次运行上面的检查命令，直到路径正确。

## 2. 使用 Wizard 连接电机

1. 关闭正在运行的 `reachy-mini-daemon` 和所有连接串口的 Python 进程。
2. 打开机器人电源，并将 USB 线连接到电脑。
3. 打开 DYNAMIXEL Wizard。
4. 在扫描设置中选择：

```text
Protocol Version: 2.0
Baudrate:         1000000
Port:             COM3
```

5. 扫描并确认可以看到需要校准的 ID `17`、`18`、`19`、`20`。

## 3. 记录基准位置

最容易复现的测量方式是先令被测电机的 `Homing Offset(20) = 0`，然后再读取
位置。此步骤必须在力矩关闭时执行。

对于每个需要校准的手臂电机：

1. 选择对应 ID。
2. 设置 `Torque Enable(64) = 0`。
3. 读取并备份原始的 `Homing Offset(20)`、`Min Position Limit(52)` 和
   `Max Position Limit(48)`。
4. 将 `Homing Offset(20)` 暂时写为 `0`。
5. 手动将手臂掰到期望的初始姿态，记录 `Present Position(132)` 为
   `P_home`。
6. 手动移动到两个机械安全端点，分别记录为 `P_a` 和 `P_b`。
7. 计算：

```text
P_min = min(P_a, P_b)
P_max = max(P_a, P_b)
```

> 如果测量时没有将 `Homing Offset` 清为 `0`，则必须记录测量时的
> `O_measure`，先将每个测量值换回基准值：
>
> ```text
> P_base = P_measured - O_measure
> ```
>
> 后续公式只使用 `P_base`。

## 4. 计算 YAML 数值

下面的计算以第 3 步得到的、`Homing Offset = 0` 时的
`P_home`、`P_min` 和 `P_max` 为输入。

### 4.1 选择合法硬件 offset

理想情况下，希望新的 home 刚好变成控制器零点 `2048`：

```text
O_ideal = 2048 - P_home
```

同时，offset 还必须保证电机和限位合法：

```text
O_low  = max(-1024, -P_min)
O_high = min( 1024, 4095 - P_max)
```

如果 `O_low > O_high`，说明完整测得运动范围无法通过软件平移落入单圈位置
范围；必须缩小可用活动范围，或重新处理机械安装。

否则选择最接近理想值的合法 offset：

```text
offset = clamp(O_ideal, O_low, O_high)
```

### 4.2 计算限位与软件零点

```text
P_home_new = P_home + offset
lower_limit = P_min + offset
upper_limit = P_max + offset

software_zero_offset_rad = (P_home_new - 2048) * 2 * pi / 4096
```

写入前必须核对：

```text
-1024 <= offset <= 1024
0 <= lower_limit < upper_limit <= 4095
lower_limit <= P_home_new <= upper_limit
```

### 4.3 可重复使用的计算脚本

将下列脚本中的测量值替换为新的 Wizard 读数，即可计算 YAML 配置：

```python
import math

measurements = {
    "left_arm_1": {"home": 2056, "low": 800, "high": 4095},
    "left_arm_2": {"home": 3968, "low": 2850, "high": 4120},
    "right_arm_1": {"home": 1023, "low": -30, "high": 3041},
    "right_arm_2": {"home": 3190, "low": 3080, "high": 4400},
}

for name, value in measurements.items():
    home = value["home"]
    low = min(value["low"], value["high"])
    high = max(value["low"], value["high"])

    ideal = 2048 - home
    allowed_low = max(-1024, -low)
    allowed_high = min(1024, 4095 - high)
    if allowed_low > allowed_high:
        raise ValueError(f"{name}: no legal full-range mapping")

    offset = min(max(ideal, allowed_low), allowed_high)
    new_home = home + offset
    new_low = low + offset
    new_high = high + offset
    software_zero = (new_home - 2048) * 2.0 * math.pi / 4096.0

    print(
        name,
        f"offset={offset}",
        f"software_zero_offset_rad={software_zero:.7f}",
        f"lower_limit={new_low}",
        f"upper_limit={new_high}",
        f"wizard_home_after_flash={new_home}",
    )
```

## 5. 本机当前四个手臂的计算结果

以下结果采用一个必要前提：记录这些原始 home 和安全端点时，
`Homing Offset(20) = 0`。如果测量当时已有非零 offset，请不要直接烧写
下表数值；应按照第 3 步重新测量，或先使用 `P_base = P_measured - O_measure`
换算后重新计算。

| 电机 | 原始 home | 原始安全范围 | `offset` | `software_zero_offset_rad` | 新限位 | 烧写后 home 读数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `left_arm_1` / 17 | 2056 | 800 .. 4095 | -8 | 0.0000000 | 792 .. 4087 | 2048 |
| `left_arm_2` / 18 | 3968 | 2850 .. 4120 | -1024 | 1.3744468 | 1826 .. 3096 | 2944 |
| `right_arm_1` / 19 | 1023 | -30 .. 3041 | 1024 | -0.0015340 | 994 .. 4065 | 2047 |
| `right_arm_2` / 20 | 3190 | 3080 .. 4400 | -1024 | 0.1810097 | 2056 .. 3376 | 2166 |

对应 YAML 片段为：

```yaml
  - left_arm_1:
      id: 17
      offset: -8
      software_zero_offset_rad: 0.0
      lower_limit: 792
      upper_limit: 4087

  - left_arm_2:
      id: 18
      offset: -1024
      software_zero_offset_rad: 1.3744468
      lower_limit: 1826
      upper_limit: 3096

  - right_arm_1:
      id: 19
      offset: 1024
      software_zero_offset_rad: -0.0015340
      lower_limit: 994
      upper_limit: 4065

  - right_arm_2:
      id: 20
      offset: -1024
      software_zero_offset_rad: 0.1810097
      lower_limit: 2056
      upper_limit: 3376
```

`software_zero_offset_rad` 不会由 `setup_motor` 烧写进电机；它只会在 daemon
运行时被读取，用于 SDK 目标角度与电机角度之间的映射。

## 6. 烧写硬件配置

确认 Wizard 和 daemon 均已关闭后，在项目目录中运行：

```powershell
conda activate reach-mini-latest
cd E:\workspace\lab\reachy_mini

python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml left_arm_1 COM3 --update-config
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml left_arm_2 COM3 --update-config
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml right_arm_1 COM3 --update-config
python -m reachy_mini.tools.setup_motor .\src\reachy_mini\assets\config\hardware_config.yaml right_arm_2 COM3 --update-config
```

该脚本会写入：

- `offset`
- `lower_limit`
- `upper_limit`
- `operating_mode`
- PID 及其他电机参数

## 7. 使用 Wizard 进行烧写后核对

烧写脚本结束后先不要启动 daemon。重新打开 Wizard，并保持力矩关闭：

1. 读取各手臂的 `Homing Offset(20)`，应与 YAML 的 `offset` 相等。
2. 读取 `Min Position Limit(52)` 与 `Max Position Limit(48)`，应与 YAML
   相等。
3. 手动将每个手臂摆回期望 home，读取 `Present Position(132)`，应接近
   表格的“烧写后 home 读数”。

新的 Wizard home 读数不一定是 `2048`。例如 ID 18 会接近 `2944`；
daemon 通过 `software_zero_offset_rad` 将这一姿态对外表示成 SDK 的
`0 rad`，这是正常结果。

## 8. 首次启动验证

先关闭 Wizard，再以不执行唤醒动作的方式启动 daemon：

```powershell
cd E:\workspace\lab\reachy_mini

reachy-mini-daemon --serialport COM3 --hardware-config-filepath .\src\reachy_mini\assets\config\hardware_config.yaml --no-wake-up-on-start
```

检查 daemon 没有配置读取、电机缺失或硬件错误后，停止该进程。托住双臂，
再进行正常启动：

```powershell
reachy-mini-daemon --serialport COM3 --hardware-config-filepath .\src\reachy_mini\assets\config\hardware_config.yaml
```

正常启动会执行 `wake_up()`，并将双臂移动到逻辑零点：

```python
left_arm = [0.0, 0.0]
right_arm = [0.0, 0.0]
```

此时应观察物理姿态是否回到第 3 步选择的 home，而不是观察 Wizard 的读数
是否等于最初记录的数值。

## 9. 诊断与回退

### 电机上电后立刻向边界运动

立即停止 daemon 或切断电机供电。检查：

- `software_zero_offset_rad` 的正负号是否正确。
- `lower_limit` 与 `upper_limit` 是否使用了烧写后的新坐标。
- daemon 实际加载的是否为当前项目中的 YAML。

### `setup_motor` 显示通过，但姿态仍错误

`setup_motor` 只验证 EEPROM 中写入的字段是否和 YAML 相等；它不能判断
选择的物理 home 是否正确。回到第 3 步，在力矩关闭和 `Homing Offset = 0`
的条件下重新测量。

### daemon 启动后配置被改回去

daemon 启动前会根据当前导入的 `reachy_mini` 包内配置检查电机，并在需要
时重新烧写。重新执行第 1 步中的导入路径检查，确保没有运行到另一个
Python 环境或另一个源码副本。

### 软件映射无法覆盖完整机械范围

如果计算得到 `O_low > O_high`，完整物理范围无法放入 Position Mode 可用
的 `0 .. 4095` 坐标范围。软件零点只改变逻辑 home，不会突破电机的单圈
硬限位；此时只能缩小安全活动范围，或改变机械安装位置。

## 参考

- [DYNAMIXEL Wizard 读取参数说明](./wizard.md)
- [ROBOTIS XL330-M077-T: Homing Offset(20)](https://emanual.robotis.com/docs/en/dxl/x/xl330-m077/#homing-offset20)
- [ROBOTIS XL330-M077-T: Min/Max Position Limit(48, 52)](https://emanual.robotis.com/docs/en/dxl/x/xl330-m077/#minmax-position-limit48-52)
- 本项目配置文件：`src/reachy_mini/assets/config/hardware_config.yaml`
- 本项目电机设置脚本：`src/reachy_mini/tools/setup_motor.py`
- 本项目 daemon 映射实现：`src/reachy_mini/daemon/backend/robot/backend.py`

# reach-mini-latest 与 reachy-mini 环境差异

生成时间：2026-05-18

## 结论

`reach-mini-latest` 和 `reachy-mini` 的普通 Python 依赖大部分一致。真正影响双臂魔改链路的关键差异是：

1. `reach-mini-latest` 的 `reachy_mini_motor_controller` 来自本地魔改 `rmmc` wheel。
2. 两个环境当前都把 `reachy_mini` 以 editable 模式指向 `E:\workspace\lab\reachy_mini`，所以双臂适配主要来自这份源码，而不是来自 PyPI 官方包。
3. `reach-mini-latest` 多了 `maturin/pytest/pytest-asyncio`，方便编译本地 Rust wheel 和跑测试。
4. `reachy-mini` 多了 `opencv-python/cv2-enumerate-cameras`，如果要跑相机相关示例，可以保留或补装。

## 包级差异

只在 `reach-mini-latest` 中：

- `maturin==1.13.3`
- `pytest==9.0.3`
- `pytest-asyncio==1.3.0`
- `pluggy==1.6.0`
- `iniconfig==2.3.0`

只在 `reachy-mini` 中：

- `opencv-python==4.13.0.92`
- `cv2-enumerate-cameras==1.3.3`

版本不同：

- `pip`：`reach-mini-latest` 是 `26.1.1`，`reachy-mini` 是 `26.0.1`

关键本地来源：

- `reach-mini-latest`：
  - `reachy_mini` editable: `E:\workspace\lab\reachy_mini`
  - `reachy_mini_motor_controller`: `E:\workspace\lab\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl`
- `reachy-mini`：
  - `reachy_mini` editable: `E:\workspace\lab\reachy_mini`
  - `reachy_mini_motor_controller`: 普通 `reachy_mini_motor_controller==1.5.5` 安装，没有本地 wheel 直连记录

## 导出文件说明

- `reach-mini-latest.full.yml`：完整环境导出，带本机 prefix。
- `reach-mini-latest.full.no-prefix.yml`：去掉 prefix 的共享版，适合 `conda env create`。
- `reach-mini-latest.from-history.yml`：只记录 conda 显式安装项，主要用于查看环境骨架。
- `reach-mini-latest.explicit-win64.txt`：Windows 64 位 conda 包精确复刻文件。
- `reach-mini-latest.pip-freeze.txt`：pip 层完整冻结，包含 editable 源码和本地 wheel 路径。
- `reach-mini-latest.local-overrides.requirements.txt`：给别人从官方环境更新到魔改环境时最重要的覆盖安装项。
- `reachy-mini.official.*`：官方环境的对照导出。

## 新电脑从零安装

假设别人也把项目放在：

- `E:\workspace\lab\reachy_mini`
- `E:\workspace\lab\rmmc`

先创建环境：

```bat
conda env create -f E:\workspace\lab\reachy_mini\env_exports\reach-mini-latest.full.no-prefix.yml
conda activate reach-mini-latest
conda env config vars set PYTHONNOUSERSITE=1
conda deactivate
conda activate reach-mini-latest
```

再覆盖成本地魔改源码和本地 `rmmc` wheel：

```bat
cd /d E:\workspace\lab\reachy_mini
python -m pip install -e .
python -m pip install --force-reinstall E:\workspace\lab\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

如果对方路径不同，需要把上面的 `E:\workspace\lab\...` 改成自己的实际路径。

## 从已有 reachy-mini 环境更新

如果对方已经有官方 `reachy-mini` 环境，不一定要新建环境。可以直接：

```bat
conda activate reachy-mini
conda env config vars set PYTHONNOUSERSITE=1
conda deactivate
conda activate reachy-mini

cd /d E:\workspace\lab\reachy_mini
python -m pip install -e .
python -m pip install maturin pytest pytest-asyncio
python -m pip install --force-reinstall E:\workspace\lab\rmmc\target\wheels\reachy_mini_motor_controller-1.5.5-cp312-cp312-win_amd64.whl
```

`opencv-python` 和 `cv2-enumerate-cameras` 在官方环境里已有，可以保留；它们不影响双臂动作链路。

## 验证命令

```bat
conda activate reach-mini-latest
cd /d E:\workspace\lab\reachy_mini

python -m pip show reachy-mini reachy-mini-motor-controller
python -c "import reachy_mini, reachy_mini_motor_controller; print(reachy_mini.__file__); print(reachy_mini_motor_controller.__file__)"
python .\action_call\play_emotion_action.py --list
```

接机器人后：

```bat
reachy-mini-daemon --serialport COM3
```

另开一个终端：

```bat
conda activate reach-mini-latest
cd /d E:\workspace\lab\reachy_mini
python .\action_call\play_emotion_action.py --emotion cheerful --no-sound
```

## 注意

`conda env export` 不能完整表达“这份环境依赖本地魔改源码”这一点，所以只导入 yml 还不够。必须额外执行 `pip install -e .` 和本地 `rmmc` wheel 的 `--force-reinstall`，否则可能退回官方 PyPI 版本。

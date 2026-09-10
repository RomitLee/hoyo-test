# 梦幻西游背包与装备属性浮窗检测与识别

> 当前手动装备识别采用“完整截图 → YOLO定位浮窗 → OCR读取属性”的两阶段方案。YOLO权重训练完成前，程序会明确提示模型不可用，不会把整张游戏截图直接交给OCR。

## 0. 手动装备图片识别（完整截图 MVP）

在桌面程序的“AI分析”页面点击“装备识别”即可：

```text
选择游戏完整截图 / 粘贴剪贴板图片
  → YOLO检测 equipment_tooltip
  → 按检测框裁剪装备属性浮窗
  → RapidOCR读取裁剪图文字
  → 规则解析名称、类型、等级和属性
  → 生成识别结果图片
```

这一步解决了“用户提供的是整个游戏画面，OCR无法主动找到装备浮窗”的问题。YOLO只负责定位区域，不负责识别“紫香乌金裙”等具体名称；文字内容仍由OCR和解析器负责。

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[equipment]"
```

Python 3.13 环境使用 `ultralytics`、`rapidocr>=3.9,<4` 与 `onnxruntime>=1.20`。训练好的模型必须放在：

```text
models/equipment_tooltip/best.pt
```

识别结果默认写入：

```text
runtime/equipment/recognition_cards/       # 结果卡片
runtime/equipment/detected_regions/        # YOLO裁剪出的浮窗，便于检查识别区域
runtime/equipment/recognition_inputs/      # 剪贴板图片副本
```

如果模型不存在、Ultralytics未安装、YOLO未检测到浮窗，界面会显示明确原因，并且不会对整张截图做OCR，避免把地图、人物、道具栏文字误当成装备属性。

## 1. YOLO 实时浮窗检测（实时分析链路）

本版本已将装备属性浮窗检测正式切换为 YOLO，并扩展为一个双类别模型：

```text
0 equipment_tooltip：装备属性浮窗
1 inventory_panel：打开的背包/道具行囊主面板
```

旧版 `src/hoyo_analyzer/equipment_detector.py` 的 OpenCV 规则检测器仍保留，用于回归测试和手动调试，但默认配置不会再使用它。

## 2. 当前检测链路

```text
Windows Graphics Capture
  → 实时帧
  → YOLO 一次推理
  → inventory_panel 稳定
  → inventory_opened 事件
  → 鼠标是否位于左侧装备槽位
  → equipment_tooltip 稳定
  → equipment_tooltip_opened 事件
  → 保存浮窗裁剪图
  → 后续 OCR + 装备名称库
```

YOLO 不负责识别“紫香乌金裙”“乾坤帽”等装备名称，也不负责读取属性文字。装备名称后续通过 OCR 和装备名称库处理。

右侧道具栏不属于当前装备识别目标。推荐将右侧道具说明浮窗作为负样本，避免它被识别为 `equipment_tooltip`。

## 3. 数据集目录

```text
datasets/mhxy_ui_detection/
├── README.md
├── dataset.yaml
├── inbox/                 # 原始图片和标签放这里
├── images/
│   ├── train/
│   ├── val/
│   └── test/
└── labels/
    ├── train/
    ├── val/
    └── test/
```

原始数据目录：

```text
C:\Users\李锐\Documents\hoyo-test\datasets\mhxy_ui_detection\inbox\
```

## 4. 标注规则

YOLO 标签文件与图片同名，例如：

```text
frame_000001.png
frame_000001.txt
```

标签格式：

```text
class_id x_center y_center width height
```

坐标均为 0～1 之间的归一化坐标。

### 只有背包打开，没有属性浮窗

只标注背包区域，类别编号为 `1`：

```text
1 x_center y_center width height
```

### 背包和属性浮窗同时出现

同一个标签文件中写两行：

```text
1 背包区域坐标
0 装备属性浮窗坐标
```

示例：

```text
1 0.450 0.510 0.760 0.820
0 0.720 0.440 0.380 0.600
```

### 没有背包和属性浮窗

可以作为负样本，只放图片，不放标签文件，例如：

- 背包关闭；
- 地图界面；
- 战斗界面；
- 聊天框；
- 鼠标停在右侧道具栏；
- 没有装备属性浮窗的背包界面。

## 5. 训练

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[yolo]"
```

划分数据集：

```powershell
.\.venv\Scripts\python.exe scripts\split_yolo_dataset.py
```

如果希望保留 `inbox` 中的原始文件：

```powershell
.\.venv\Scripts\python.exe scripts\split_yolo_dataset.py --copy
```

开始训练：

```powershell
.\.venv\Scripts\python.exe scripts\train_equipment_yolo.py
```

训练结果默认位于：

```text
runs/yolo/mhxy_ui_detection_v1/
```

将最佳权重复制到：

```text
C:\Users\李锐\Documents\hoyo-test\models\equipment_tooltip\best.pt
```

然后重新启动桌面程序。

## 6. 程序配置

```toml
[equipment]
enabled = true
backend = "yolo"
model_path = "models/equipment_tooltip/best.pt"
confidence = 0.65
image_size = 640
device = "auto"
stable_frames = 3
```

程序每帧只执行一次 YOLO 推理，然后分别跟踪两个类别的连续帧稳定状态。模型不存在、Ultralytics 未安装或模型加载失败时，程序不会崩溃，会返回 `yolo_model_not_found`、`yolo_model_load_failed` 等原因。

## 7. 相关代码

| 文件 | 作用 |
| --- | --- |
| `src/hoyo_analyzer/equipment_region.py` | YOLO单次检测完整截图中的 `equipment_tooltip`，返回最高置信度框 |
| `src/hoyo_analyzer/equipment_recognition.py` | YOLO裁剪浮窗后调用OCR并解析装备属性 |
| `src/hoyo_analyzer/equipment_dialog.py` | 上传/粘贴完整截图、后台识别和结果卡片 |
| `src/hoyo_analyzer/yolo_detector.py` | 实时分析链路中检测 `equipment_tooltip` 和 `inventory_panel`，并做稳定判断 |
| `src/hoyo_analyzer/perception.py` | 将背包检测、鼠标装备槽门控和浮窗检测融合为 Observation |
| `src/hoyo_analyzer/event_machine.py` | 将稳定的 `inventory_open` 转换为打开/关闭背包事件 |
| `src/hoyo_analyzer/cli.py` | 根据配置构造 YOLO 或旧版 OpenCV 检测器 |
| `scripts/split_yolo_dataset.py` | 检查并划分 train/val/test |
| `scripts/train_equipment_yolo.py` | 启动 Ultralytics YOLO 训练 |
| `datasets/mhxy_ui_detection/dataset.yaml` | 双类别 YOLO 数据集配置 |
| `models/equipment_tooltip/best.pt` | 训练完成后放置的模型权重 |

## 8. 数据采集建议

建议同时收集：

- 只有背包的图片，用于 `inventory_panel`；
- 背包和属性浮窗同时出现的图片，用于两个类别；
- 背包关闭、战斗、地图、右侧道具栏等负样本；
- 不同窗口分辨率、位置、缩放比例和浮窗位置。

不要把同一段视频的连续帧随机分散到训练集和验证集，否则验证结果可能虚高。更好的做法是按视频段或采集批次分组后再放入 train/val/test。

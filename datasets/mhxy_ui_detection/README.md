# 梦幻西游界面 YOLO 数据集

本目录用于训练一个双类别 YOLO 模型，识别梦幻西游中的背包和装备属性浮窗。

## 类别

当前有两个类别：

```text
0 equipment_tooltip
1 inventory_panel
```

说明：

- `equipment_tooltip`：装备属性浮窗；
- `inventory_panel`：已经打开的背包/道具行囊主面板。

不要把“紫香乌金裙”“乾坤帽”等装备名称拆成 YOLO 类别。YOLO 只负责定位界面区域，未来再使用 OCR + 装备名称库读取具体名称和属性。

## 开始收集图片

把游戏截图或从实时采集流抽取的图片放到：

```text
datasets/mhxy_ui_detection/inbox/
```

建议图片覆盖：

- 背包关闭；
- 背包打开但没有悬停装备；
- 背包打开且鼠标悬停左侧装备；
- 属性浮窗出现在不同位置；
- 不同装备栏位；
- 不同游戏分辨率：800×600、640×480、1024×768、1280×960；
- 不同窗口大小、缩放比例和画质；
- 浮窗贴边或部分被窗口边界裁切；
- 鼠标停在右侧道具栏等容易误检的场景。

## 标注方式

使用 LabelImg、CVAT、Roboflow Annotate 或其他支持 YOLO 格式的工具。标签文件与图片同名，格式为：

```text
class_id x_center y_center width height
```

坐标均为 0～1 之间的归一化值。

### 只有背包打开，没有属性浮窗

只标注背包区域：

```text
1 0.450 0.510 0.760 0.820
```

### 背包和属性浮窗同时出现

在同一个标签文件中写两行：

```text
1 背包区域的坐标
0 装备属性浮窗的坐标
```

例如：

```text
1 0.450 0.510 0.760 0.820
0 0.720 0.440 0.380 0.600
```

### 没有背包和属性浮窗

可以作为负样本，只放图片，不放 `.txt` 文件。例如战斗、地图、聊天框、背包关闭等画面。

右侧道具栏的说明浮窗当前不作为 `equipment_tooltip`，建议不标注它，让它作为负样本，避免误识别。

## 划分和训练

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\split_yolo_dataset.py
.\.venv\Scripts\python.exe scripts\train_equipment_yolo.py
```

如果希望保留 `inbox` 中的原始文件，可以使用：

```powershell
.\.venv\Scripts\python.exe scripts\split_yolo_dataset.py --copy
```

训练完成后，默认最佳模型位置为：

```text
models/equipment_tooltip/best.pt
```

然后重新启动桌面程序。训练完成前，程序会显示/记录 `yolo_model_not_found`，不会识别装备浮窗或背包 YOLO 信号。

## 数据集注意事项

不要把同一段视频的连续帧随机分散到训练集和验证集，这会造成验证结果虚高。更好的做法是按视频段或采集批次分组后再放入 train/val/test。

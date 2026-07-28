# Python 与 NumPy 10 天速学方案

> 适用对象：有其他语言编程经验、能够快速理解基础语法，希望尽快进入 OpenCV、YOLO、OCR 和视觉数据处理实践的人。
>
> 建议投入：**10 天，共 20～30 小时**。每天约 2～3 小时；如果可以全职学习，可压缩到 5～7 天，但不建议跳过练习和测试。

> 配套逐日教程：[Python 与 NumPy 10 天逐日教程](./docs/python-numpy/README.md)

---

## 1. 学习目标

本阶段不是为了“学完 Python”，而是为了达到下面这个工程目标：

> 能够使用 Python 和 NumPy，可靠地处理图像数组、检测框和结构化识别结果，并为下一阶段的 OpenCV 与 YOLO 开发打好基础。

完成后，你应当能够独立处理下面的数据：

```python
frames: np.ndarray       # (N, H, W, 3)，一批图像
boxes: np.ndarray        # (N, M, 4)，每帧 M 个检测框
scores: np.ndarray       # (N, M)，置信度
class_ids: np.ndarray    # (N, M)，类别编号
```

并完成如下流水线：

```text
输入数据
→ 校验 shape 和 dtype
→ 图像归一化
→ 检测框裁剪
→ 置信度过滤
→ IoU 计算
→ 多帧稳定性判断
→ 转换为 dataclass
→ 导出 JSON
```

### 本阶段刻意跳过的内容

以下内容只需在遇到问题时快速查阅，不安排大段时间学习：

- 变量、运算符和基础数据类型
- `if`、`for`、`while` 的基础写法
- 函数和类的基础语法
- 字符串的普通操作
- 面向对象概念入门
- 复杂 GUI、Web 开发和数据库框架
- Python 冷门语法与元编程

---

## 2. 推荐技术栈

建议使用以下工具建立轻量、可复现的开发环境：

| 工具 | 用途 | 本阶段要求 |
|---|---|---|
| Python 3.13 | 运行环境 | 会运行脚本、模块和类型检查 |
| uv | Python 版本、虚拟环境和依赖管理 | 会初始化、添加依赖、运行命令 |
| NumPy 2.x | 数组与视觉数学计算 | 本阶段核心 |
| pytest | 自动化测试 | 会写参数化测试和异常测试 |
| Ruff | 代码检查与格式化 | 保持代码质量 |
| Pyright | 静态类型检查 | 尽早发现接口和类型问题 |
| Git | 版本控制 | 小步提交、可回退 |
| VS Code / PyCharm | 编辑器 | 二选一即可 |

### 为什么暂时不安装太多库

第一阶段建议只安装 NumPy 和开发工具，暂不安装：

- Anaconda
- Pandas
- SciPy
- OpenCV
- PyTorch
- Ultralytics YOLO
- PaddleOCR
- CUDA Toolkit

原因是先把“Python 工程结构 + 数组思维”学扎实。过早引入大量视觉框架，会让问题被库的接口和环境依赖掩盖，不利于理解图像数据究竟怎样流动。

---

## 3. Day 0：环境和项目骨架

### 3.1 安装 uv

在 PowerShell 中执行：

```powershell
winget install --id=astral-sh.uv -e
```

安装后重新打开终端，检查：

```powershell
uv --version
```

### 3.2 在当前项目中配置 Python

```powershell
cd C:\Users\李锐\Documents\hoyo-test

uv python install 3.13
uv init --bare
uv python pin 3.13
uv add numpy
uv add --dev pytest ruff pyright
```

> 如果仓库已经有 `pyproject.toml`，不要重复执行 `uv init`，直接添加依赖即可。

### 3.3 建议目录结构

```text
hoyo-test/
├── pyproject.toml
├── uv.lock
├── .python-version
├── src/
│   └── hoyo_vision/
│       ├── __init__.py
│       ├── models.py
│       ├── image_ops.py
│       ├── box_ops.py
│       └── pipeline.py
├── tests/
│   ├── test_image_ops.py
│   └── test_box_ops.py
├── examples/
│   └── numpy_demo.py
└── benchmarks/
    └── vectorization_benchmark.py
```

### 3.4 环境验收

```powershell
uv run python --version
uv run python -c "import numpy as np; print(np.__version__)"
uv run pytest
uv run ruff check .
uv run pyright
```

### Day 0 验收标准

- [ ] 不依赖系统全局 Python，也能在项目中运行代码。
- [ ] 能解释 `pyproject.toml`、`uv.lock` 和 `.python-version` 的作用。
- [ ] 新电脑克隆仓库后，可以按文档重建环境。
- [ ] 知道正式依赖和开发依赖的区别。

---

## 4. 10 天总览

| 天数 | 建议时长 | 核心主题 | 当天产出 |
|---|---:|---|---|
| Day 0 | 1～2 小时 | 环境和项目骨架 | 可复现的 Python 项目 |
| Day 1 | 2～3 小时 | Python 对象模型与容器 | 对象引用实验和数据清洗函数 |
| Day 2 | 2～3 小时 | dataclass、类型、模块 | 检测结果领域模型 |
| Day 3 | 2～3 小时 | 异常、日志、生成器、测试 | 模拟视频帧处理器 |
| Day 4 | 2～3 小时 | ndarray、shape、dtype、内存 | 图像数组检查工具 |
| Day 5 | 2～3 小时 | 索引、切片、视图与副本 | ROI 裁剪工具 |
| Day 6 | 2～3 小时 | broadcasting、axis、向量化 | 批量像素处理函数 |
| Day 7 | 2～3 小时 | reshape、transpose、批处理 | HWC/CHW 转换与基准测试 |
| Day 8 | 2～3 小时 | 检测框坐标计算 | box_ops 模块 |
| Day 9 | 2～3 小时 | IoU 与多帧稳定性 | 向量化 IoU 和稳定性判断 |
| Day 10 | 3～5 小时 | 综合项目 | NumPy 视觉数据处理器 |

---

# 第一部分：Python 工程能力

## 5. Day 1：对象模型、容器和常用表达式

不要重新背语法。重点理解 Python 与 Java、C#、C++ 等语言容易产生差异的地方。

### 5.1 必须掌握

1. **变量保存的是对象引用**，不是固定类型的值槽。
2. 可变对象与不可变对象的区别。
3. 赋值、浅拷贝、深拷贝之间的区别。
4. 函数参数的对象共享语义。
5. 可变默认参数陷阱。
6. 列表推导式、字典推导式和集合推导式。
7. `enumerate`、`zip`、`any`、`all`、`sorted`。
8. 字典的安全访问与结构化转换。

### 5.2 必做实验

```python
import copy

source = [[1, 2], [3, 4]]
assigned = source
shallow = source.copy()
deep = copy.deepcopy(source)

source[0][0] = 99

print(assigned)
print(shallow)
print(deep)
```

运行前先预测输出，再验证。关键不是记住答案，而是形成对对象引用关系的直觉。

### 5.3 必须避开的写法

```python
def add_detection(item, results=[]):
    results.append(item)
    return results
```

推荐：

```python
def add_detection(item, results=None):
    if results is None:
        results = []
    results.append(item)
    return results
```

### 5.4 当天练习

给定一批原始识别结果：

```python
raw_detections = [
    {"label": "task_panel", "score": 0.93, "box": [100, 80, 500, 420]},
    {"label": "button", "score": 0.31, "box": [20, 20, 80, 60]},
    {"label": "npc", "score": 0.87, "box": [600, 200, 750, 600]},
]
```

完成：

- 过滤 `score < 0.5` 的结果。
- 按置信度从高到低排序。
- 提取所有标签。
- 判断是否存在 `task_panel`。
- 判断所有保留结果是否都具有四个坐标。
- 不修改原始列表。

### Day 1 验收标准

- [ ] 能解释赋值、浅拷贝和深拷贝。
- [ ] 不会写出可变默认参数。
- [ ] 能使用推导式、`enumerate`、`zip`、`any` 和 `all` 简化代码。
- [ ] 能在不污染原始数据的情况下完成清洗和转换。

---

## 6. Day 2：dataclass、类型标注和模块设计

视觉项目中，数据会在采集、检测、OCR、规则判断和存储模块之间传递。必须尽早定义稳定的数据结构，避免到处传递含义不明的字典。

### 6.1 建议领域模型

在 `src/hoyo_vision/models.py` 中定义：

```python
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(slots=True, frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: BoundingBox
```

### 6.2 为什么使用 `slots=True` 和 `frozen=True`

- `slots=True`：减少实例的动态属性开销，也能避免误写不存在的属性。
- `frozen=True`：对象创建后不可随意修改，降低状态在流水线中被意外污染的风险。
- 检测结果更适合被当作“值对象”，而不是可随处修改的共享状态。

### 6.3 类型标注重点

掌握以下内容即可：

- `list[str]`、`dict[str, float]`
- `tuple[int, int]`
- `str | None`
- `Iterable`、`Iterator`、`Sequence`
- `TypeAlias`
- `numpy.typing.NDArray`

示例：

```python
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

ImageArray: TypeAlias = npt.NDArray[np.uint8]
FloatArray: TypeAlias = npt.NDArray[np.float32]


def normalize_image(image: ImageArray) -> FloatArray:
    return image.astype(np.float32) / 255.0
```

> NumPy 类型标注不能完全在静态阶段表达每一个维度，因此 shape 仍然需要运行时校验。

### 6.4 模块职责建议

| 文件 | 职责 |
|---|---|
| `models.py` | 数据模型，不做复杂计算 |
| `image_ops.py` | 图像数组转换与校验 |
| `box_ops.py` | 检测框数学计算 |
| `pipeline.py` | 串联处理步骤 |
| `tests/` | 单元测试和边界测试 |

### Day 2 验收标准

- [ ] 能使用 dataclass 建模，而不是所有数据都用字典表示。
- [ ] 公共函数具有参数类型和返回值类型。
- [ ] 模块之间没有循环依赖。
- [ ] 能使用 Pyright 检查明显的类型错误。

---

## 7. Day 3：异常、日志、生成器和 pytest

### 7.1 异常处理原则

只在能够处理问题的层级捕获异常，不要使用下面这种方式隐藏错误：

```python
try:
    process_frame(frame)
except Exception:
    pass
```

建议定义明确异常：

```python
class InvalidImageError(ValueError):
    """输入图像的形状或类型不符合约定。"""
```

```python
def validate_image(image: np.ndarray) -> None:
    if image.ndim != 3 or image.shape[2] != 3:
        raise InvalidImageError(
            f"expected image shape (H, W, 3), got {image.shape}"
        )
    if image.dtype != np.uint8:
        raise InvalidImageError(
            f"expected uint8 image, got {image.dtype}"
        )
```

### 7.2 使用日志，不使用散落的 print

```python
import logging

logger = logging.getLogger(__name__)


def process_frame(frame_id: int, frame: np.ndarray) -> None:
    logger.debug(
        "processing frame id=%d shape=%s dtype=%s",
        frame_id,
        frame.shape,
        frame.dtype,
    )
```

日志至少记录：

- 帧编号
- 图像尺寸
- 输入来源
- 处理耗时
- 检测数量
- 异常原因

### 7.3 用生成器模拟逐帧处理

```python
from collections.abc import Iterator

import numpy as np


def generate_frames(count: int, height: int, width: int) -> Iterator[np.ndarray]:
    for _ in range(count):
        yield np.zeros((height, width, 3), dtype=np.uint8)
```

生成器适合视频帧流，因为不需要一次性把所有帧加载到内存。

### 7.4 pytest 最小示例

```python
import numpy as np
import pytest

from hoyo_vision.image_ops import normalize_image, validate_image


def test_normalize_image() -> None:
    image = np.array([0, 127, 255], dtype=np.uint8)
    result = normalize_image(image)

    assert result.dtype == np.float32
    assert np.allclose(result, [0.0, 127 / 255, 1.0])


def test_validate_image_rejects_grayscale() -> None:
    image = np.zeros((100, 100), dtype=np.uint8)

    with pytest.raises(ValueError):
        validate_image(image)
```

### Day 3 验收标准

- [ ] 错误输入能够产生明确异常，而不是静默失败。
- [ ] 日志能够定位具体帧和具体处理阶段。
- [ ] 能用生成器按需产生或消费帧。
- [ ] 能为正常、边界和异常情况编写测试。

---

# 第二部分：NumPy 数组思维

## 8. Day 4：ndarray、shape、dtype 和内存

NumPy 的重点不是 API 数量，而是始终清楚：

1. 当前数组每个轴代表什么？
2. 当前 `shape` 是什么？
3. 当前 `dtype` 是什么？
4. 当前操作是否产生副本？
5. 内存是否连续？

### 8.1 常见视觉数据布局

| 数据 | shape | 含义 |
|---|---|---|
| 灰度图 | `(H, W)` | 高、宽 |
| 彩色图 | `(H, W, C)` | 高、宽、通道 |
| 图像批次 | `(N, H, W, C)` | 批次、高、宽、通道 |
| 模型输入批次 | `(N, C, H, W)` | 批次、通道、高、宽 |
| 检测框 | `(N, 4)` | N 个 `x1,y1,x2,y2` |
| 置信度 | `(N,)` | N 个分数 |
| IoU 矩阵 | `(N, M)` | 两组框两两比较 |

### 8.2 必须观察的属性

```python
import numpy as np

image = np.zeros((720, 1280, 3), dtype=np.uint8)

print(image.ndim)
print(image.shape)
print(image.dtype)
print(image.size)
print(image.itemsize)
print(image.nbytes)
print(image.strides)
print(image.flags.c_contiguous)
```

### 8.3 `uint8` 溢出陷阱

```python
pixels = np.array([250], dtype=np.uint8)
print(pixels + 10)
```

结果不会是 260，因为 `uint8` 的范围只有 0～255。

涉及减法、均值、归一化或颜色运算时，通常先转换：

```python
working = pixels.astype(np.float32)
result = working + 10
```

或者显式裁剪：

```python
result = np.clip(working + 10, 0, 255).astype(np.uint8)
```

### 8.4 当天练习：图像检查器

编写：

```python
def describe_array(array: np.ndarray) -> dict[str, object]:
    ...
```

返回：

```python
{
    "shape": (720, 1280, 3),
    "ndim": 3,
    "dtype": "uint8",
    "size": 2764800,
    "nbytes": 2764800,
    "strides": (...),
    "c_contiguous": True,
}
```

### Day 4 验收标准

- [ ] 看到一个视觉数组时，第一反应是检查 shape 和 dtype。
- [ ] 能估算一批视频帧占用多少内存。
- [ ] 知道为什么像素计算常转换到 `float32`。
- [ ] 能解释 `strides` 和连续内存的基本含义。

---

## 9. Day 5：索引、切片、ROI、视图与副本

### 9.1 ROI 裁剪

```python
roi = image[y1:y2, x1:x2]
```

注意坐标顺序：

- 检测框通常写作 `(x1, y1, x2, y2)`。
- NumPy 图像索引通常写作 `[y1:y2, x1:x2]`。

这是视觉项目最常见的错误之一。

### 9.2 基础切片通常返回视图

```python
roi = image[100:300, 200:500]
print(np.shares_memory(image, roi))
```

如果修改 `roi`，原图可能随之变化：

```python
roi[:] = 0
```

如果需要独立图像：

```python
roi_copy = image[100:300, 200:500].copy()
```

### 9.3 高级索引通常会创建副本

```python
selected = image[[0, 2, 4]]
```

学习时使用：

```python
np.shares_memory(source, result)
```

验证结果是否共享内存。

### 9.4 布尔索引

```python
scores = np.array([0.92, 0.31, 0.81, 0.45], dtype=np.float32)
boxes = np.array(
    [
        [10, 10, 100, 100],
        [20, 20, 80, 80],
        [200, 100, 400, 350],
        [0, 0, 20, 20],
    ],
    dtype=np.float32,
)

mask = scores >= 0.5
kept_boxes = boxes[mask]
kept_scores = scores[mask]
```

### 9.5 当天练习：安全 ROI

实现：

```python
def crop_roi(
    image: np.ndarray,
    box: np.ndarray,
    *,
    copy: bool = True,
) -> np.ndarray:
    ...
```

要求：

- 接收 `xyxy` 坐标。
- 坐标超出图像时自动裁剪。
- 非法框应抛出明确异常。
- 可以选择返回视图或副本。
- 测试负坐标、越界坐标、零面积和正常情况。

### Day 5 验收标准

- [ ] 不再混淆 `(x, y)` 和 NumPy 的 `[y, x]`。
- [ ] 明确知道何时需要 `.copy()`。
- [ ] 会使用布尔掩码批量过滤结果。
- [ ] 能处理 ROI 越界和空区域。

---

## 10. Day 6：broadcasting、axis 和向量化

### 10.1 broadcasting 的理解方式

不要死记规则。先从尾部对齐 shape：

```text
图像：      (720, 1280, 3)
通道均值：            (3,)
结果：      (720, 1280, 3)
```

示例：

```python
image = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
mean = np.array([123.0, 117.0, 104.0], dtype=np.float32)
std = np.array([58.0, 57.0, 57.0], dtype=np.float32)

normalized = (image.astype(np.float32) - mean) / std
```

### 10.2 `axis` 的核心含义

`axis` 指定被消除或被操作的维度。

```python
image.mean(axis=0)       # 消除高度轴
image.mean(axis=1)       # 消除宽度轴
image.mean(axis=(0, 1))  # 每个颜色通道的平均值，结果 shape 为 (3,)
```

每次写 NumPy 操作前，先在注释中写预期 shape：

```python
# image: (H, W, C)
# channel_mean: (C,)
channel_mean = image.mean(axis=(0, 1))
```

### 10.3 不要逐像素写 Python 循环

不推荐：

```python
for y in range(image.shape[0]):
    for x in range(image.shape[1]):
        image[y, x] = image[y, x] / 255.0
```

推荐：

```python
normalized = image.astype(np.float32) / 255.0
```

Python 循环可以用于“帧级控制流程”，但像素级和检测框级数学应优先向量化。

### 10.4 必做练习

1. 对一批 `(N, H, W, 3)` 图像计算每帧、每通道均值。
2. 将低于阈值的像素设为 0。
3. 使用 `np.where` 构造二值掩码。
4. 对 N 个检测框批量计算宽、高和面积。
5. 不使用框级 `for` 循环，过滤面积小于阈值的框。

### Day 6 验收标准

- [ ] 能在纸上判断两个 shape 是否可以广播。
- [ ] 能预测 `mean`、`sum`、`max` 操作后的 shape。
- [ ] 能用布尔掩码和向量化替换像素级循环。
- [ ] 能解释 `keepdims=True` 的用途。

---

## 11. Day 7：reshape、transpose 和模型输入布局

### 11.1 HWC 转 CHW

OpenCV 常见图像布局：

```text
(H, W, C)
```

深度学习模型常见输入布局：

```text
(C, H, W)
```

转换：

```python
chw = image.transpose(2, 0, 1)
```

批量 NHWC 转 NCHW：

```python
nchw = frames.transpose(0, 3, 1, 2)
```

更强调轴语义时，也可以使用：

```python
nchw = np.moveaxis(frames, -1, 1)
```

### 11.2 `reshape` 不能代替 `transpose`

- `reshape` 改变形状解释，通常不改变元素逻辑顺序。
- `transpose` 改变轴顺序。
- 把 HWC 转成 CHW 必须交换轴，不能简单 `reshape`。

### 11.3 连续内存

转置后数组可能不是 C 连续的：

```python
chw = image.transpose(2, 0, 1)
print(chw.flags.c_contiguous)
```

某些推理库或底层接口要求连续内存，可以使用：

```python
chw = np.ascontiguousarray(chw)
```

### 11.4 `stack` 与 `concatenate`

```python
batch = np.stack([image1, image2], axis=0)
```

`stack` 会创建一个新轴；`concatenate` 沿已有轴连接。

### 11.5 基准测试

在 `benchmarks/vectorization_benchmark.py` 中比较：

- Python 双层循环处理像素。
- NumPy 向量化处理像素。
- 分别对 10、100 张模拟图像计时。

计时建议使用 `time.perf_counter()`，并在报告中记录：

- 输入 shape
- dtype
- 循环耗时
- 向量化耗时
- 加速倍数

### Day 7 验收标准

- [ ] 能正确完成 HWC↔CHW 和 NHWC↔NCHW。
- [ ] 不会用 `reshape` 冒充轴交换。
- [ ] 能检查和修复数组的连续性。
- [ ] 能正确选择 `stack` 或 `concatenate`。

---

# 第三部分：视觉数学与综合项目

## 12. Day 8：检测框坐标系统

YOLO 项目中常见的检测框格式：

```text
xyxy = [x1, y1, x2, y2]
xywh = [center_x, center_y, width, height]
```

坐标还可能分为：

- 像素绝对坐标
- 0～1 的归一化坐标

### 12.1 必须实现的函数

```python
def clip_boxes_xyxy(boxes: np.ndarray, width: int, height: int) -> np.ndarray:
    ...


def xyxy_to_xywh(boxes: np.ndarray) -> np.ndarray:
    ...


def xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    ...


def normalize_boxes_xyxy(boxes: np.ndarray, width: int, height: int) -> np.ndarray:
    ...


def denormalize_boxes_xyxy(boxes: np.ndarray, width: int, height: int) -> np.ndarray:
    ...


def box_area(boxes: np.ndarray) -> np.ndarray:
    ...


def scale_boxes_xyxy(
    boxes: np.ndarray,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> np.ndarray:
    ...
```

### 12.2 shape 约定

函数统一接收：

```text
boxes: (N, 4)
```

即使只有一个框，也使用 `(1, 4)`，不要在公共接口中混用 `(4,)` 和 `(N, 4)`。

### 12.3 数值与边界规则

建议明确规定：

- 坐标计算使用 `float32`。
- 裁剪后必须满足 `0 <= x <= width` 和 `0 <= y <= height`。
- 面积使用 `max(0, x2 - x1) * max(0, y2 - y1)`。
- 零面积框是否保留由调用方决定。
- 转换函数不应意外修改原数组。

### Day 8 验收标准

- [ ] 能在 `xyxy` 与 `xywh` 之间批量转换。
- [ ] 能在绝对坐标和归一化坐标之间转换。
- [ ] 图像缩放后能正确映射检测框。
- [ ] 所有函数都覆盖空数组、单框、多框和越界测试。

---

## 13. Day 9：IoU 和多帧稳定性

### 13.1 IoU 定义

IoU（Intersection over Union）表示两个框的交集面积除以并集面积：

```text
IoU = intersection_area / union_area
```

需要实现两组检测框的两两比较：

```python
def box_iou(
    boxes_a: np.ndarray,  # (N, 4)
    boxes_b: np.ndarray,  # (M, 4)
) -> np.ndarray:          # (N, M)
    ...
```

### 13.2 向量化思路

将两组框扩展为：

```text
boxes_a[:, None, :]  → (N, 1, 4)
boxes_b[None, :, :]  → (1, M, 4)
```

利用 broadcasting 得到 `(N, M)` 的交集宽、高和面积。

参考实现骨架：

```python
import numpy as np


def box_iou(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    a = np.asarray(boxes_a, dtype=np.float32)
    b = np.asarray(boxes_b, dtype=np.float32)

    if a.ndim != 2 or a.shape[1] != 4:
        raise ValueError(f"boxes_a must have shape (N, 4), got {a.shape}")
    if b.ndim != 2 or b.shape[1] != 4:
        raise ValueError(f"boxes_b must have shape (M, 4), got {b.shape}")

    left_top = np.maximum(a[:, None, :2], b[None, :, :2])
    right_bottom = np.minimum(a[:, None, 2:], b[None, :, 2:])
    intersection_wh = np.clip(right_bottom - left_top, 0.0, None)
    intersection = intersection_wh[..., 0] * intersection_wh[..., 1]

    area_a = np.clip(a[:, 2] - a[:, 0], 0.0, None) * np.clip(
        a[:, 3] - a[:, 1], 0.0, None
    )
    area_b = np.clip(b[:, 2] - b[:, 0], 0.0, None) * np.clip(
        b[:, 3] - b[:, 1], 0.0, None
    )

    union = area_a[:, None] + area_b[None, :] - intersection
    return np.divide(
        intersection,
        union,
        out=np.zeros_like(intersection),
        where=union > 0,
    )
```

### 13.3 多帧稳定性

单帧识别可能抖动。一个目标连续多帧出现，且位置变化很小，才视为稳定。

可使用简单规则：

```text
同类别
AND IoU >= 0.7
AND 连续出现 >= 3 帧
AND 平均置信度 >= 0.6
→ stable = true
```

本阶段不用做复杂跟踪算法，先实现一个轻量稳定性判断器。

### 13.4 必测情况

- 完全重合：IoU = 1。
- 完全不相交：IoU = 0。
- 部分相交：与手算结果一致。
- 零面积框：不能产生 `NaN` 或除零错误。
- 空输入：返回正确 shape 的空矩阵。
- N 对 M：输出必须是 `(N, M)`。

### Day 9 验收标准

- [ ] 能解释为什么 IoU 输出是 `(N, M)`。
- [ ] IoU 实现中没有对 N、M 的双层 Python 循环。
- [ ] 零面积框不会导致 `NaN`。
- [ ] 能利用多帧信息过滤偶发误识别。

---

## 14. Day 10：综合项目——NumPy 视觉数据处理器

### 14.1 项目目标

构建一个不依赖 OpenCV 和 YOLO 的模拟处理器，提前实现未来视觉系统的核心数据接口。

### 14.2 输入

```python
frames: np.ndarray       # (N, H, W, 3), uint8
boxes: np.ndarray        # (N, M, 4), float32, xyxy
scores: np.ndarray       # (N, M), float32
class_ids: np.ndarray    # (N, M), int64
```

### 14.3 处理步骤

1. 校验四组输入的维度和 shape 是否匹配。
2. 校验图像是 `uint8`，检测框和置信度是浮点类型。
3. 将图像归一化到 `[0, 1]`，输出 `float32`。
4. 将检测框裁剪到图像边界内。
5. 根据置信度阈值构造布尔掩码。
6. 删除零面积和过小检测框。
7. 计算相邻帧同类检测框的 IoU。
8. 判断目标是否连续稳定出现。
9. 转换为 dataclass 对象。
10. 序列化为 JSON。
11. 输出处理日志和耗时。

### 14.4 建议输出

```json
{
  "frame_id": 15,
  "image_shape": [720, 1280, 3],
  "objects": [
    {
      "label": "task_panel",
      "confidence": 0.94,
      "bbox": [720.0, 110.0, 1120.0, 480.0],
      "area": 148000.0,
      "stable": true
    }
  ]
}
```

### 14.5 建议 API

```python
@dataclass(slots=True, frozen=True)
class PipelineConfig:
    confidence_threshold: float = 0.5
    minimum_area: float = 16.0
    stable_iou_threshold: float = 0.7
    stable_frame_count: int = 3


class VisionDataPipeline:
    def __init__(self, config: PipelineConfig) -> None:
        self._config = config

    def process_batch(
        self,
        frames: np.ndarray,
        boxes: np.ndarray,
        scores: np.ndarray,
        class_ids: np.ndarray,
    ) -> list[dict[str, object]]:
        ...
```

### 14.6 测试要求

至少包含以下测试：

- 正常输入。
- 空检测结果。
- shape 不匹配。
- 错误图像通道数。
- 错误 dtype。
- 检测框越界。
- 零面积框。
- 置信度刚好等于阈值。
- 多帧稳定目标。
- 单帧误报目标。
- JSON 可序列化。

### 14.7 质量检查

```powershell
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
```

### Day 10 验收标准

- [ ] 所有测试通过。
- [ ] Ruff 无错误。
- [ ] Pyright 无关键类型错误。
- [ ] 公共函数都有类型标注和简洁 docstring。
- [ ] 核心数组运算没有像素级或框级双层 Python 循环。
- [ ] README 中写明安装、运行、测试和示例命令。
- [ ] 输出 JSON 可作为后续 OCR、状态机或数据库模块的输入。

---

## 15. 每天的推荐学习方式

有编程经验的人不需要按“看课—记笔记—背 API”的顺序学习。建议采用以下节奏：

```text
15 分钟：阅读当天目标
30 分钟：阅读官方文档和最小示例
60～90 分钟：自己实现
30 分钟：写测试、制造错误、修正
15 分钟：提交 Git，记录当天结论
```

每一天至少创建一个 Git 提交，例如：

```text
chore: initialize python project with uv
feat: add image array validation
feat: implement roi cropping
feat: add vectorized box operations
feat: implement pairwise iou
test: cover invalid boxes and empty inputs
feat: build numpy vision data pipeline
```

---

## 16. 最重要的 NumPy 训练规则

### 规则一：运算前先预测 shape

在每一个不直观的操作前写注释：

```python
# boxes_a: (N, 4)
# boxes_b: (M, 4)
# left_top: (N, M, 2)
left_top = np.maximum(boxes_a[:, None, :2], boxes_b[None, :, :2])
```

### 规则二：同时预测 dtype

特别关注：

- `uint8` 加减法可能溢出。
- 除法后的 dtype。
- `mean`、`sum` 的类型变化。
- `astype` 是否产生副本。
- JSON 不认识部分 NumPy 标量，需要转换为 Python 标量。

### 规则三：弄清视图还是副本

当结果会被修改时，主动确认：

```python
np.shares_memory(source, result)
```

### 规则四：先保证正确，再做向量化

可先写简单循环作为参考实现，再写向量化版本，用测试比较二者输出是否一致。最终生产代码保留向量化版本，参考版本可以留在测试或 benchmark 中。

### 规则五：先定义数据契约

每个公共函数至少明确：

- 输入 shape
- 输入 dtype
- 坐标格式
- 是否修改原数组
- 输出 shape
- 异常条件

---

## 17. 常见误区

### 误区 1：记住大量 NumPy API 就算掌握

真正重要的是 shape、dtype、axis、broadcasting、内存共享和向量化思维。

### 误区 2：所有操作都追求一行代码

可读性优先。视觉数学公式可以拆成 `left_top`、`right_bottom`、`intersection`、`union` 等中间变量。

### 误区 3：看到循环就认为错误

以下循环是合理的：

- 从摄像头持续读取帧。
- 逐帧维护状态机。
- 写日志和发送结构化事件。
- 处理数量很少且逻辑复杂的业务对象。

应重点避免的是像素级或大量检测框上的 Python 双层循环。

### 误区 4：一开始就用 GPU

Python 和 NumPy 阶段主要训练数据处理和工程能力，CPU 足够。GPU 在后续 YOLO 训练和高帧率推理阶段才真正重要。

### 误区 5：只运行成功，不写测试

视觉数据特别容易出现“没有报错，但坐标和轴错了”的问题。测试应覆盖 shape、边界、空数组和数值精度。

### 误区 6：把所有东西放在一个脚本里

即便是学习项目，也要从一开始拆分模型、图像操作、检测框操作、流水线和测试，避免后期无法维护。

---

## 18. 阶段结业检查表

只有下面大部分项目都能独立完成，才建议进入 OpenCV 阶段。

### Python 工程能力

- [ ] 能使用 uv 创建和复现项目环境。
- [ ] 理解对象引用、可变性、浅拷贝和深拷贝。
- [ ] 不使用可变默认参数。
- [ ] 会使用 dataclass 表示检测结果。
- [ ] 公共接口有类型标注。
- [ ] 能使用 `pathlib` 处理路径。
- [ ] 能定义清晰异常并使用 logging。
- [ ] 能使用生成器处理连续帧。
- [ ] 能使用 pytest 编写正常、边界和异常测试。
- [ ] 能运行 Ruff 和 Pyright。

### NumPy 能力

- [ ] 看到数组能解释每一个轴的含义。
- [ ] 能预测常见操作后的 shape。
- [ ] 能正确理解和使用 `axis`。
- [ ] 能区分视图与副本。
- [ ] 会使用布尔掩码和 broadcasting。
- [ ] 能避免 `uint8` 溢出。
- [ ] 能完成 HWC/NHWC 到 CHW/NCHW 的转换。
- [ ] 能检查并创建连续内存数组。
- [ ] 能批量处理检测框。
- [ ] 能实现向量化 IoU。
- [ ] 能处理空数组、越界框和零面积框。

### 项目能力

- [ ] 能把模拟帧和模拟检测结果处理成 JSON。
- [ ] 能判断一个目标是否在多帧中稳定出现。
- [ ] 代码、测试和运行方式已提交到 Git。
- [ ] 在没有 IDE 特殊配置的情况下也能从命令行运行项目。

---

## 19. 本阶段不需要追求的内容

为了保持进度，本阶段不要求：

- 手写矩阵乘法底层实现。
- 学习 NumPy 的全部线性代数 API。
- 研究 Python 解释器源码。
- 学习复杂装饰器和元类。
- 掌握异步编程全部细节。
- 进行 GPU 编程。
- 使用多进程优化视频流水线。
- 立即接入真实 YOLO 模型。

这些内容可以在项目真正出现需求时再补。

---

## 20. 下一阶段衔接：OpenCV

完成本方案后，下一阶段建议直接进入一个小型 OpenCV 项目：

```text
读取本地图片/视频
→ 获取图像 shape 和 FPS
→ resize 与颜色转换
→ 截取固定 ROI
→ 模板匹配固定 UI 图标
→ 绘制检测框与文字
→ 输出标注视频
→ 记录结构化 JSON
```

因为已经掌握 NumPy，OpenCV 图像对象将不再是黑盒：它本质上就是一个 `ndarray`。之后再接入 YOLO 时，也能理解预处理、模型输入、检测框缩放和后处理在做什么。

---

## 21. 一句话执行建议

> 前 3 天建立可靠的 Python 工程习惯，接下来 4 天建立 NumPy 数组思维，最后 3 天围绕检测框、IoU 和结构化结果完成一个可测试的小项目；不要在基础语法和冷门 API 上消耗时间。

# Day 2：dataclass、类型标注与模块设计

> 上一课：[Day 01：对象模型、容器和常用表达式](./Day01-Python对象模型容器与常用表达式.md)
> 下一课：[Day 03：异常、日志、生成器与 pytest](./Day03-异常日志生成器与pytest.md)

## 1. 今日目标

今天把 Day 1 的“松散字典”升级为稳定的视觉领域模型。建议用时：**2～3 小时**。

完成后应能：

- 使用 `dataclass` 描述检测框、检测结果和帧结果。
- 理解 `slots=True`、`frozen=True`、`field` 和 `__post_init__`。
- 为公共函数添加实用的类型标注。
- 使用 `TypeAlias` 与 `numpy.typing.NDArray` 表达数组用途。
- 按职责拆分模块，避免循环导入。

---

## 2. 为什么不能一直使用字典

字典适合接收 JSON 或第三方模型原始输出，但不适合作为整个项目的核心模型：

```python
detection = {
    "lable": "npc",       # 拼写错误
    "score": "0.92",      # 类型不稳定
    "box": [100, 80, 20],  # 长度错误
}
```

长期传递松散字典会导致字段无法自动补全、必填字段不清晰、类型错误发现太晚、坐标含义依赖记忆。

推荐边界：

```text
外部 JSON/模型输出
→ 校验和转换
→ dataclass 领域对象
→ 业务处理
→ 显式序列化为 JSON
```

---

## 3. 第一个领域模型

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

可推导的 `width`、`height` 和 `area` 不重复存储，避免坐标和面积不同步。

---

## 4. `slots=True` 与 `frozen=True`

`slots=True`：

- 阻止随意增加未声明属性。
- 降低大量小对象的内存开销。
- 让固定字段模型更加明确。

`frozen=True`：

- 对象创建后不能重新赋值字段。
- 减少流水线中状态被意外污染。
- 适合检测结果这类值对象。

需要更新时创建新对象：

```python
from dataclasses import replace

updated = replace(detection, confidence=0.95)
```

注意：冻结不是递归不可变。冻结对象内部如果保存列表，列表仍可修改，因此优先使用元组或冻结 dataclass。

---

## 5. 使用 `__post_init__` 维护不变量

```python
from dataclasses import dataclass
import math


@dataclass(slots=True, frozen=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        values = (self.x1, self.y1, self.x2, self.y2)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("box coordinates must be finite")
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError("box must satisfy x2 >= x1 and y2 >= y1")
```

```python
@dataclass(slots=True, frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: BoundingBox

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("label must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
```

原则：

- 模型维护自身简单不变量。
- 外部格式解析由专门函数负责。
- 不在 `__post_init__` 中读取文件、请求网络或做昂贵计算。

---

## 6. 可变默认值与 `field`

```python
from dataclasses import dataclass, field


@dataclass(slots=True)
class MutableFrameResult:
    frame_id: int
    detections: list[Detection] = field(default_factory=list)
```

不要写 `detections: list[Detection] = []`，否则多个实例可能共享可变默认对象。

更稳定的跨模块模型可以直接使用元组：

```python
@dataclass(slots=True, frozen=True)
class FrameResult:
    frame_id: int
    image_shape: tuple[int, int, int]
    detections: tuple[Detection, ...] = ()
```

---

## 7. 类型标注的目标

类型标注用于表达数据契约、改善 IDE 补全、支持重构并提前发现明显错误。优先标注：

1. 公共函数参数和返回值。
2. dataclass 字段。
3. 外部输入边界。
4. 复杂局部变量。

无需给每个简单局部变量重复标注，也不要为了省事到处使用 `Any`。

---

## 8. 常用抽象类型

```python
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path


def load_labels(path: Path) -> list[str]:
    ...


def choose_best(items: Sequence[Detection]) -> Detection | None:
    ...


def iter_valid(items: Iterable[Detection]) -> Iterator[Detection]:
    ...


def parse_detection(data: Mapping[str, object]) -> Detection:
    ...
```

接口只要求调用方提供真正需要的能力：

- 只需遍历：`Iterable[T]`。
- 需要长度和索引：`Sequence[T]`。
- 只读键值：`Mapping[K, V]`。
- 确实需要修改：`list[T]` 或 `dict[K, V]`。

---

## 9. NumPy 类型标注

```python
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

ImageUInt8: TypeAlias = npt.NDArray[np.uint8]
ImageFloat32: TypeAlias = npt.NDArray[np.float32]
BoxesFloat32: TypeAlias = npt.NDArray[np.float32]
```

```python
def normalize_image(image: ImageUInt8) -> ImageFloat32:
    return image.astype(np.float32) / np.float32(255.0)
```

类型标注通常不能静态保证数组一定是 `(H, W, 3)`，仍需运行时校验：

```python
def validate_color_image(image: ImageUInt8) -> None:
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3), got {image.shape}")
```

---

## 10. 类型收窄

外部数据先检查，再进入强类型模型：

```python
from collections.abc import Mapping


def parse_label(data: Mapping[str, object]) -> str:
    raw_label = data.get("label")
    if not isinstance(raw_label, str) or not raw_label.strip():
        raise ValueError("label must be a non-empty string")
    return raw_label
```

校验后，类型检查器知道 `raw_label` 是 `str`。第三方接口返回的 `Any` 应尽快转换，不要让它扩散到整个项目。

---

## 11. 类级常量与枚举

```python
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class Detection:
    FORMAT_VERSION: ClassVar[int] = 1
    label: str
    confidence: float
    bbox: BoundingBox
```

有限业务状态可使用枚举：

```python
from enum import StrEnum


class SceneType(StrEnum):
    UNKNOWN = "unknown"
    COMBAT = "combat"
    TASK = "task"
    DIALOG = "dialog"
```

YOLO 类别可能来自模型配置，不必全部做成枚举；状态机的稳定有限状态更适合枚举。

---

## 12. 推荐模块结构

```text
src/hoyo_vision/
├── __init__.py
├── models.py       # 稳定领域对象
├── exceptions.py   # 自定义异常
├── image_ops.py    # 图像数组操作
├── box_ops.py      # 检测框数学
├── serializers.py  # JSON 转换
└── pipeline.py     # 流程编排
```

职责约束：

- `models.py` 不读取文件、不调用模型。
- `image_ops.py` 不理解具体游戏业务。
- `box_ops.py` 只处理坐标和框。
- `pipeline.py` 组合模块，不重复实现算法。
- 不创建无边界的 `utils.py` 垃圾桶。

依赖方向：

```text
models / exceptions
       ↑
image_ops / box_ops / serializers
       ↑
pipeline
       ↑
application / examples
```

底层模块不能反向依赖上层流程。出现循环导入时优先重新划分职责。

---

## 13. 包内导入与运行方式

包内导入：

```python
from .models import BoundingBox, Detection
```

外部示例：

```python
from hoyo_vision.models import BoundingBox, Detection
```

不要在脚本里修改 `sys.path`。使用项目环境和模块方式运行：

```powershell
uv run python -m examples.day02_models_demo
```

---

## 14. 显式 JSON 序列化

```python
from typing import TypedDict


class DetectionJson(TypedDict):
    label: str
    confidence: float
    bbox: list[float]


def detection_to_json(detection: Detection) -> DetectionJson:
    box = detection.bbox
    return {
        "label": detection.label,
        "confidence": detection.confidence,
        "bbox": [box.x1, box.y1, box.x2, box.y2],
    }
```

显式转换比无条件 `asdict()` 更容易控制字段命名、版本兼容、NumPy 标量转换和内部字段暴露。

---

## 15. 今日综合任务：视觉领域模型

实现：

```text
BoundingBox
Detection
FrameResult
```

要求：

1. 坐标必须是有限数值。
2. `x2 >= x1`、`y2 >= y1`。
3. 标签不能为空。
4. 置信度在 `[0, 1]`。
5. 帧编号不能为负数。
6. 图像 shape 必须为 `(H, W, 3)` 且 H、W 大于 0。
7. `FrameResult` 使用元组保存检测结果。
8. 提供 JSON 转换函数。
9. 提供从 Day 1 字典格式转换为 `Detection` 的解析函数。

---

## 16. 建议测试

```python
import pytest


def test_bounding_box_area() -> None:
    box = BoundingBox(10.0, 20.0, 30.0, 50.0)
    assert box.width == 20.0
    assert box.height == 30.0
    assert box.area == 600.0


def test_detection_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError):
        Detection("npc", 1.2, BoundingBox(0, 0, 10, 10))
```

至少覆盖正常框、零面积框、反向坐标、`NaN`、无穷值、空标签、置信度边界、非法 shape、JSON 字段和冻结对象。

---

## 17. 今日验收清单

- [ ] 能解释 dataclass 相比字典的价值。
- [ ] 会使用 `slots=True` 和 `frozen=True`。
- [ ] 知道冻结不是递归不可变。
- [ ] 会使用 `field(default_factory=...)`。
- [ ] 会在 `__post_init__` 中维护简单不变量。
- [ ] 公共函数具有参数和返回值类型。
- [ ] 能使用 `Iterable`、`Sequence` 和 `Mapping`。
- [ ] 知道 NumPy 类型标注不能代替 shape 校验。
- [ ] 模块依赖方向清晰，无循环导入。
- [ ] 完成领域模型、解析器和 JSON 转换器。

## 18. 今日最低交付物

```text
src/hoyo_vision/models.py
src/hoyo_vision/serializers.py
tests/test_models.py
examples/day02_models_demo.py
```

建议提交信息：

```text
feat: add typed visual domain models
```

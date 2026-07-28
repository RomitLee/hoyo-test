# Day 10：NumPy 视觉数据处理器综合项目

> 上一课：[Day 09：IoU 与多帧稳定性](./Day09-IoU与多帧稳定性.md)
> 返回：[Python 与 NumPy 10 天逐日教程](./README.md)

## 1. 今日目标

今天不再引入大量新语法，而是把前 9 天的知识组装成一个**可测试、可观测、可扩展的视觉数据处理流水线**。建议用时：**3～5 小时**。

最终流水线接收一批图像与模型检测结果，完成：

```text
输入校验
→ 图像归一化
→ 检测框裁剪
→ 置信度/类别/面积过滤
→ IoU 与多帧稳定性判断
→ dataclass 领域对象
→ JSON 序列化
→ 日志与耗时统计
```

完成后应能：

- 明确定义图像、检测框、分数和类别数组的输入契约。
- 把纯 NumPy 函数与有状态的稳定性判断器分开。
- 用配置对象控制阈值，而不是在业务代码中散落魔法数字。
- 对空检测、越界框、非法 shape、错误 dtype 等情况给出确定行为。
- 将处理结果转换为可供 OCR、状态机、数据库或 API 使用的 JSON。
- 使用 pytest、Ruff 和 Pyright 完成基本工程验收。

---

## 2. 项目边界

本项目只负责“模型输出之后的数据处理”，暂不负责：

- OBS 或摄像头的视频采集。
- OpenCV 解码和颜色空间转换。
- YOLO 模型加载、推理与训练。
- OCR 文本识别。
- 键盘、鼠标或机械臂控制。
- 大模型决策。

这种边界很重要。先把数组和结构化数据处理层做成稳定模块，后续替换采集源或模型时不必重写整个系统。

---

## 3. 输入契约

本练习使用定长批次形式：

```python
frames: np.ndarray       # (N, H, W, 3), uint8
boxes: np.ndarray        # (N, M, 4), float32，xyxy 绝对坐标
scores: np.ndarray       # (N, M), float32
class_ids: np.ndarray    # (N, M), int64
```

含义：

- `N`：帧数。
- `H, W`：图像高度和宽度。
- `M`：每帧预留的最大检测数。
- `boxes[n, m]`：第 `n` 帧第 `m` 个检测框。
- `scores[n, m]`：对应置信度。
- `class_ids[n, m]`：对应类别编号。

为了表示不足 `M` 个检测的帧，约定无效槽位满足以下任一条件：

```text
score < 0
或 class_id < 0
```

真实模型经常返回“每帧检测数量不同”的列表。接入 YOLO 时可以先逐帧处理，也可以补齐成上述批次格式；本项目优先练习批量数组操作。

---

## 4. 输出契约

每个有效检测转换为领域对象：

```python
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class DetectionResult:
    frame_index: int
    class_id: int
    class_name: str
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]
    area: float
    stable: bool = False
    consecutive_hits: int = 1

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

一批处理结果还应包含运行信息：

```python
@dataclass(frozen=True, slots=True)
class BatchResult:
    frame_count: int
    input_detection_count: int
    output_detection_count: int
    elapsed_ms: float
    detections: tuple[DetectionResult, ...]
```

原则：

- NumPy 标量在输出前转换成 Python 的 `int`、`float`、`bool`。
- 元组会被 `json.dumps` 序列化为 JSON 数组。
- 输出不携带完整图像，避免 JSON 体积失控。
- 原始数组和中间调试信息由调用方按需保存。

---

## 5. 推荐目录

```text
src/hoyo_vision/
├── __init__.py
├── exceptions.py       # 自定义异常
├── models.py           # dataclass 领域模型
├── validation.py       # shape、dtype 和数值校验
├── image_ops.py        # 图像归一化等纯函数
├── box_ops.py          # 框裁剪、面积、IoU
├── stability.py        # 跨帧状态
├── serialization.py    # JSON 转换
└── pipeline.py         # 流程编排

tests/
├── test_validation.py
├── test_image_ops.py
├── test_box_ops.py
├── test_stability.py
└── test_pipeline.py

examples/
└── day10_pipeline_demo.py

benchmarks/
└── vectorization_benchmark.py
```

不要一开始把所有实现塞进 `pipeline.py`。纯计算函数独立后更容易测试，也更容易在 OpenCV、YOLO 或实时视频代码中复用。

---

## 6. 配置对象

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    confidence_threshold: float = 0.60
    minimum_box_area: float = 25.0
    allowed_class_ids: frozenset[int] | None = None
    stability_iou_threshold: float = 0.50
    stable_after_hits: int = 3
    max_missed_frames: int = 1

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold 必须位于 [0, 1]")
        if self.minimum_box_area < 0.0:
            raise ValueError("minimum_box_area 不能为负数")
        if not 0.0 <= self.stability_iou_threshold <= 1.0:
            raise ValueError("stability_iou_threshold 必须位于 [0, 1]")
        if self.stable_after_hits < 1:
            raise ValueError("stable_after_hits 必须至少为 1")
        if self.max_missed_frames < 0:
            raise ValueError("max_missed_frames 不能为负数")
```

配置使用 `frozen=True`，让一次流水线运行期间的规则保持不变。`allowed_class_ids=None` 表示允许全部类别。

---

## 7. 输入校验

定义专用异常：

```python
class VisionDataError(ValueError):
    """视觉数据不满足接口契约。"""
```

批次校验函数：

```python
import numpy as np
from numpy.typing import NDArray

UInt8Array = NDArray[np.uint8]
FloatArray = NDArray[np.floating]
IntArray = NDArray[np.integer]


def validate_batch(
    frames: UInt8Array,
    boxes: FloatArray,
    scores: FloatArray,
    class_ids: IntArray,
) -> None:
    if not isinstance(frames, np.ndarray):
        raise VisionDataError("frames 必须是 ndarray")
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise VisionDataError(f"frames 应为 (N,H,W,3)，实际为 {frames.shape}")
    if frames.dtype != np.uint8:
        raise VisionDataError(f"frames 应为 uint8，实际为 {frames.dtype}")

    if boxes.ndim != 3 or boxes.shape[-1] != 4:
        raise VisionDataError(f"boxes 应为 (N,M,4)，实际为 {boxes.shape}")
    if scores.ndim != 2:
        raise VisionDataError(f"scores 应为 (N,M)，实际为 {scores.shape}")
    if class_ids.ndim != 2:
        raise VisionDataError(f"class_ids 应为 (N,M)，实际为 {class_ids.shape}")

    n, m, _ = boxes.shape
    if frames.shape[0] != n:
        raise VisionDataError("frames 与 boxes 的帧数不一致")
    if scores.shape != (n, m) or class_ids.shape != (n, m):
        raise VisionDataError("boxes、scores 与 class_ids 的前两维必须一致")

    if not np.issubdtype(boxes.dtype, np.floating):
        raise VisionDataError("boxes 必须使用浮点 dtype")
    if not np.issubdtype(scores.dtype, np.floating):
        raise VisionDataError("scores 必须使用浮点 dtype")
    if not np.issubdtype(class_ids.dtype, np.integer):
        raise VisionDataError("class_ids 必须使用整数 dtype")

    if not np.isfinite(boxes).all():
        raise VisionDataError("boxes 包含 NaN 或无穷值")
    if not np.isfinite(scores).all():
        raise VisionDataError("scores 包含 NaN 或无穷值")
```

这里不要悄悄替调用方修正所有错误。接口边界应尽早失败，并让错误信息包含实际 shape 或 dtype。

---

## 8. 图像归一化

```python
import numpy as np
from numpy.typing import NDArray


def normalize_frames(frames: NDArray[np.uint8]) -> NDArray[np.float32]:
    return frames.astype(np.float32) / np.float32(255.0)
```

验收点：

- 输入不被修改。
- 输出 dtype 是 `float32`。
- 输出 shape 与输入一致。
- 输出范围位于 `[0,1]`。
- 避免先对 `uint8` 做可能溢出的计算。

如果当前流水线只处理检测框、不需要图像张量，可以通过配置选择不执行归一化；不要为了“流程完整”制造不必要的大数组副本。

---

## 9. 框裁剪与面积

```python

def clip_boxes_xyxy(
    boxes: NDArray[np.floating],
    image_width: int,
    image_height: int,
) -> NDArray[np.float32]:
    clipped = boxes.astype(np.float32, copy=True)
    clipped[..., 0::2] = np.clip(clipped[..., 0::2], 0.0, float(image_width))
    clipped[..., 1::2] = np.clip(clipped[..., 1::2], 0.0, float(image_height))
    return clipped


def box_areas_xyxy(boxes: NDArray[np.floating]) -> NDArray[np.float32]:
    widths = np.maximum(0.0, boxes[..., 2] - boxes[..., 0])
    heights = np.maximum(0.0, boxes[..., 3] - boxes[..., 1])
    return (widths * heights).astype(np.float32, copy=False)
```

裁剪后再计算面积。越界框不应产生负面积；`x2 <= x1` 或 `y2 <= y1` 的框面积为 0，并在过滤阶段移除。

---

## 10. 向量化过滤掩码

```python

def build_detection_mask(
    scores: NDArray[np.floating],
    class_ids: NDArray[np.integer],
    areas: NDArray[np.floating],
    config: PipelineConfig,
) -> NDArray[np.bool_]:
    mask = (
        (scores >= config.confidence_threshold)
        & (class_ids >= 0)
        & (areas >= config.minimum_box_area)
    )

    if config.allowed_class_ids is not None:
        allowed = np.asarray(tuple(config.allowed_class_ids), dtype=class_ids.dtype)
        mask &= np.isin(class_ids, allowed)

    return mask
```

`mask` 的 shape 是 `(N,M)`，可同时过滤 `boxes`、`scores`、`class_ids` 和 `areas`：

```python
selected_boxes = clipped_boxes[mask]       # (K,4)
selected_scores = scores[mask]             # (K,)
selected_class_ids = class_ids[mask]        # (K,)
selected_areas = areas[mask]                # (K,)
```

但扁平化会丢失帧号，因此还需要：

```python
frame_indices, detection_indices = np.nonzero(mask)
```

其中 `frame_indices[k]` 对应第 `k` 个保留检测来自哪一帧。

---

## 11. 流水线编排类

下面是一个最小骨架。跨帧稳定性判断器沿用 Day 9 的实现，这里只展示编排职责：

```python
import logging
from collections.abc import Mapping
from time import perf_counter

logger = logging.getLogger(__name__)


class VisionDataPipeline:
    def __init__(
        self,
        config: PipelineConfig,
        class_names: Mapping[int, str],
        stability_tracker: "StabilityTracker",
    ) -> None:
        self._config = config
        self._class_names = dict(class_names)
        self._stability_tracker = stability_tracker

    def process_batch(
        self,
        frames: UInt8Array,
        boxes: FloatArray,
        scores: FloatArray,
        class_ids: IntArray,
    ) -> BatchResult:
        started = perf_counter()
        validate_batch(frames, boxes, scores, class_ids)

        frame_count, image_height, image_width, _ = frames.shape
        clipped = clip_boxes_xyxy(boxes, image_width, image_height)
        areas = box_areas_xyxy(clipped)
        mask = build_detection_mask(
            scores=scores,
            class_ids=class_ids,
            areas=areas,
            config=self._config,
        )

        results: list[DetectionResult] = []
        for frame_index in range(frame_count):
            frame_mask = mask[frame_index]
            frame_boxes = clipped[frame_index, frame_mask]
            frame_scores = scores[frame_index, frame_mask]
            frame_classes = class_ids[frame_index, frame_mask]
            frame_areas = areas[frame_index, frame_mask]

            stable_states = self._stability_tracker.update(
                boxes=frame_boxes,
                scores=frame_scores,
                class_ids=frame_classes,
            )

            for i in range(frame_boxes.shape[0]):
                box = frame_boxes[i]
                state = stable_states[i]
                class_id = int(frame_classes[i])
                results.append(
                    DetectionResult(
                        frame_index=frame_index,
                        class_id=class_id,
                        class_name=self._class_names.get(class_id, f"unknown_{class_id}"),
                        confidence=float(frame_scores[i]),
                        bbox_xyxy=tuple(float(value) for value in box),
                        area=float(frame_areas[i]),
                        stable=bool(state.stable),
                        consecutive_hits=int(state.consecutive_hits),
                    )
                )

        elapsed_ms = (perf_counter() - started) * 1000.0
        output = BatchResult(
            frame_count=frame_count,
            input_detection_count=int(np.count_nonzero(scores >= 0.0)),
            output_detection_count=len(results),
            elapsed_ms=elapsed_ms,
            detections=tuple(results),
        )
        logger.info(
            "batch processed frames=%d input=%d output=%d elapsed_ms=%.3f",
            output.frame_count,
            output.input_detection_count,
            output.output_detection_count,
            output.elapsed_ms,
        )
        return output
```

注意：

- `for frame_index in range(frame_count)` 是合理循环，因为稳定性状态具有时间顺序。
- 单帧内部的框裁剪、面积和过滤已经向量化。
- 不要为了追求“零循环”而破坏可读性和时序语义。
- `normalize_frames` 是否调用，应由后续模型输入需求决定。

---

## 12. JSON 序列化

```python
import json
from dataclasses import asdict


def batch_result_to_json(result: BatchResult, *, indent: int | None = 2) -> str:
    return json.dumps(
        asdict(result),
        ensure_ascii=False,
        indent=indent,
        allow_nan=False,
    )
```

示例输出：

```json
{
  "frame_count": 2,
  "input_detection_count": 4,
  "output_detection_count": 2,
  "elapsed_ms": 0.83,
  "detections": [
    {
      "frame_index": 0,
      "class_id": 1,
      "class_name": "任务按钮",
      "confidence": 0.93,
      "bbox_xyxy": [120.0, 80.0, 260.0, 132.0],
      "area": 7280.0,
      "stable": false,
      "consecutive_hits": 1
    }
  ]
}
```

使用 `allow_nan=False`，可以阻止不合法的 `NaN` 或无穷值悄悄进入下游系统。

---

## 13. 最小演示数据

```python
import numpy as np

frames = np.zeros((3, 720, 1280, 3), dtype=np.uint8)
boxes = np.array(
    [
        [[100, 80, 260, 140], [-1, -1, -1, -1]],
        [[103, 82, 263, 142], [-1, -1, -1, -1]],
        [[105, 83, 265, 143], [900, 700, 1400, 800]],
    ],
    dtype=np.float32,
)
scores = np.array(
    [[0.91, -1.0], [0.94, -1.0], [0.93, 0.40]],
    dtype=np.float32,
)
class_ids = np.array(
    [[1, -1], [1, -1], [1, 2]],
    dtype=np.int64,
)
```

预期：

- 类别 1 的框连续出现，第三帧可达到稳定状态。
- 类别 2 的框越界且置信度不足，应被过滤。
- 无效槽位不应出现在结果中。
- 输入 `boxes` 不应被原地修改。

---

## 14. 实施顺序

### 阶段 A：先固定接口

1. 创建 `PipelineConfig`、`DetectionResult` 和 `BatchResult`。
2. 写类型标注和 docstring。
3. 先写测试中的输入与预期，不急着写完整实现。

### 阶段 B：实现纯函数

1. `validate_batch`。
2. `normalize_frames`。
3. `clip_boxes_xyxy`。
4. `box_areas_xyxy`。
5. `build_detection_mask`。
6. Day 9 的 `pairwise_iou`。

每写完一个函数就运行对应测试，不要最后一次性调试。

### 阶段 C：接入状态

1. 接入稳定性判断器。
2. 明确状态何时创建、更新和删除。
3. 测试一次漏检、连续漏检、类别变化和位置跳变。

### 阶段 D：编排与输出

1. 实现 `VisionDataPipeline.process_batch`。
2. 增加结构化日志和耗时。
3. 实现 JSON 序列化。
4. 编写最小 demo。

---

## 15. 测试矩阵

| 类别 | 测试场景 | 预期 |
|---|---|---|
| 正常 | 单帧单框 | 输出一个领域对象 |
| 正常 | 三帧同类近似框 | 第三帧达到稳定条件 |
| 空输入 | `N=0` | 返回空结果，不崩溃 |
| 空检测 | `M=0` | 每帧均无检测 |
| 无效槽位 | score/class 为负 | 被过滤 |
| shape | frames 不是四维 | 抛出 `VisionDataError` |
| shape | boxes 尾维不是 4 | 抛出 `VisionDataError` |
| dtype | frames 是 float | 抛出 `VisionDataError` |
| 数值 | boxes 含 NaN | 抛出 `VisionDataError` |
| 边界 | 框超出图像 | 被裁剪到图像范围 |
| 边界 | 负宽或负高 | 面积为 0 并过滤 |
| 阈值 | score 恰好等于阈值 | 按约定保留 |
| 类别 | 不在白名单 | 被过滤 |
| 序列化 | 中文类别名 | JSON 保留中文 |
| 序列化 | NumPy 标量 | 成功转成标准 JSON |
| 副作用 | 输入 boxes | 处理前后完全一致 |

---

## 16. pytest 示例

```python
import numpy as np
import pytest


def test_clip_boxes_does_not_modify_input() -> None:
    boxes = np.array([[-10, 5, 120, 80]], dtype=np.float32)
    original = boxes.copy()

    actual = clip_boxes_xyxy(boxes, image_width=100, image_height=60)

    np.testing.assert_array_equal(boxes, original)
    np.testing.assert_array_equal(
        actual,
        np.array([[0, 5, 100, 60]], dtype=np.float32),
    )


def test_rejects_wrong_frame_dtype() -> None:
    frames = np.zeros((1, 10, 10, 3), dtype=np.float32)
    boxes = np.empty((1, 0, 4), dtype=np.float32)
    scores = np.empty((1, 0), dtype=np.float32)
    class_ids = np.empty((1, 0), dtype=np.int64)

    with pytest.raises(VisionDataError, match="uint8"):
        validate_batch(frames, boxes, scores, class_ids)
```

对数组使用 `numpy.testing`，不要用普通的 `==` 比较整个数组。

---

## 17. 工程检查命令

如果项目使用 `uv`：

```bash
uv add numpy
uv add --dev pytest ruff pyright
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```

建议在 `pyproject.toml` 中统一配置工具，避免每位开发者使用不同规则。

最低要求：

```text
Ruff：无错误
Pyright：核心 src 目录无类型错误
pytest：全部通过
```

---

## 18. 性能测量

不要只凭感觉判断性能。使用 `perf_counter` 对相同输入重复执行：

```python
from time import perf_counter

started = perf_counter()
for _ in range(100):
    _ = box_areas_xyxy(boxes)
elapsed_ms = (perf_counter() - started) * 1000.0
print(f"100 次耗时：{elapsed_ms:.3f} ms")
```

建议比较：

- Python 双层循环计算面积。
- NumPy 向量化计算面积。
- 不同批量大小下的耗时。
- `float32` 和 `float64` 的内存占用。
- 是否因为不必要的 `copy()` 增加耗时。

实时项目关注的不是某个函数“足够快”，而是整条链路能否在目标帧率下稳定完成。未来接入 OBS 后，应分别记录采集、预处理、推理、后处理、OCR 和决策耗时。

---

## 19. 日志建议

正常批次记录 `INFO`：

```text
batch processed frames=8 input=24 output=11 elapsed_ms=1.824
```

可恢复的异常记录 `WARNING`：

```text
unknown class id=99 frame=12
```

违反输入契约直接抛异常，并由程序入口记录堆栈。不要在底层函数中捕获所有 `Exception` 后返回空结果，否则会把真实数据错误伪装成“没有检测到目标”。

日志中避免直接输出整张图像数组或数千个框；记录 shape、dtype、数量、阈值和耗时通常已经足够。

---

## 20. 综合练习

实现一个可运行的 `examples/day10_pipeline_demo.py`：

1. 构造 5 帧假图像。
2. 构造任务按钮、NPC、对话框三类检测结果。
3. 插入一个越界框、一个低置信度框和一个零面积框。
4. 使用类别白名单只保留任务按钮和对话框。
5. 连续三帧识别到任务按钮后标记稳定。
6. 将结果输出到控制台和 `output/day10-result.json`。
7. 日志显示输入数、过滤后数量和耗时。
8. 测试确认所有输入数组未被修改。

加分项：

- 为输出 JSON 定义版本号，例如 `schema_version: "1.0"`。
- 增加 `source_id`、时间戳和帧序号。
- 使用生成器逐批处理大量帧，避免一次性加载全部视频。
- 建立 benchmark，比较循环和向量化版本。

---

## 21. 常见设计错误

### 错误 1：流水线类承担所有数学计算

后果是难测试、难复用。应把数组操作拆成纯函数，流水线只做顺序编排。

### 错误 2：对输入数组原地裁剪

模型输出可能还要用于画框或调试。默认返回副本，除非接口明确声明会原地修改。

### 错误 3：所有数据都转成 Python 列表再处理

这会失去 NumPy 的 shape 语义和向量化优势。计算阶段保留 ndarray，只在输出边界转换。

### 错误 4：盲目消除全部循环

跨帧状态本来就是时序过程。应消除大量独立元素上的低效循环，而不是牺牲正确性。

### 错误 5：阈值散落在代码里

阈值属于配置和实验参数，应集中保存，并在日志中记录实际值。

### 错误 6：异常时返回空列表

空列表可能意味着“本帧没有目标”，也可能意味着“数据损坏”。两者必须区分。

---

## 22. 今日验收清单

- [ ] 已建立清晰的 `src`、`tests`、`examples` 目录。
- [ ] 输入 shape、dtype、坐标格式都有明确契约。
- [ ] 所有公共函数有类型标注。
- [ ] 框裁剪、面积、过滤和 IoU 主要使用 NumPy 向量化。
- [ ] 输入数组默认不被修改。
- [ ] 空帧、空检测、越界框和零面积框均有测试。
- [ ] 非法 shape、dtype、NaN 会产生明确异常。
- [ ] 多帧稳定性逻辑有独立测试。
- [ ] NumPy 标量可正常序列化为 JSON。
- [ ] 日志包含数量和耗时，不输出巨型数组。
- [ ] `ruff check`、`pyright` 和 `pytest` 通过。
- [ ] demo 能生成一份结构化 JSON 结果。

---

## 23. 十天结业标准

如果你可以不照抄答案，独立完成下面任务，就已经具备进入 OpenCV 阶段的 Python 与 NumPy 基础：

1. 看见数组接口时能立即说明每个轴的语义。
2. 能预测常见索引、broadcasting、reshape 和 transpose 的输出 shape。
3. 能判断切片是视图还是副本，并控制副作用。
4. 能在 `uint8`、`float32` 和整数类型之间安全转换。
5. 能实现检测框格式转换、裁剪、面积和 IoU。
6. 能将不稳定的逐帧检测转换为较稳定的结构化事件。
7. 能用 dataclass、日志、异常和 pytest 构建可维护模块。
8. 能把结果输出为下游系统可消费的 JSON。

接下来建议进入：

```text
OpenCV 图像读取与颜色空间
→ ROI、缩放、模板匹配和截图
→ OBS/摄像头实时帧采集
→ YOLO 推理接口
→ OCR
→ 状态机和结构化事件
```

## 24. 今日最低交付物

```text
src/hoyo_vision/pipeline.py
src/hoyo_vision/serialization.py
tests/test_pipeline.py
examples/day10_pipeline_demo.py
output/day10-result.json
```

建议提交信息：

```text
feat: complete NumPy vision data pipeline
```

完成以上验收后，回到[课程索引](./README.md)，开始规划 OpenCV 学习阶段。

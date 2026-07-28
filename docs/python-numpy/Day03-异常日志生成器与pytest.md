# Day 3：异常、日志、生成器与 pytest

> 上一课：[Day 02：dataclass、类型标注与模块设计](./Day02-dataclass类型标注与模块设计.md)
> 下一课：[Day 04：ndarray、shape、dtype 与内存](./Day04-ndarray-shape-dtype与内存.md)

## 1. 今日目标

今天让视觉程序从“能运行”升级为“出错时能定位、输入异常时能解释、连续处理时不爆内存”。建议用时：**2～3 小时**。

完成后应能：

- 设计清晰的异常层次。
- 在正确层级捕获和转换异常。
- 使用 `logging` 记录帧编号、shape、耗时和错误上下文。
- 使用生成器模拟和处理视频帧流。
- 使用 pytest 编写正常、边界和异常测试。

---

## 2. 异常是接口的一部分

不要隐藏问题：

```python
try:
    process_frame(frame)
except Exception:
    pass
```

正确目标：

```text
错误输入 → 明确异常类型和可读信息
第三方异常 → 保留原始原因并转换为领域异常
顶层循环 → 记录上下文并决定继续、重试或停止
```

---

## 3. 自定义异常层次

在 `exceptions.py` 中：

```python
class HoyoVisionError(Exception):
    """项目可预期异常的基类。"""


class InvalidImageError(HoyoVisionError, ValueError):
    """图像 shape 或 dtype 不符合契约。"""


class InvalidDetectionError(HoyoVisionError, ValueError):
    """检测结果字段或数值不合法。"""


class FrameProcessingError(HoyoVisionError):
    """某一帧无法完成处理。"""
```

按调用方的处理策略划分异常，不要为每一行代码创建一个异常类。

---

## 4. 保留异常链

```python
def parse_score(raw_score: object) -> float:
    try:
        return float(raw_score)
    except (TypeError, ValueError) as error:
        raise InvalidDetectionError(
            f"invalid confidence value: {raw_score!r}"
        ) from error
```

`raise ... from ...` 同时保留业务解释和底层原始原因。

---

## 5. 在能处理的层级捕获

底层校验直接抛错：

```python
def validate_frame(frame) -> None:
    if frame is None:
        raise InvalidImageError("frame must not be None")
```

处理层补充上下文：

```python
def process_frame(frame_id: int, frame):
    try:
        validate_frame(frame)
        return run_pipeline(frame)
    except InvalidImageError as error:
        raise FrameProcessingError(
            f"frame {frame_id} is invalid"
        ) from error
```

应用顶层决定策略：

```python
for frame_id, frame in frame_stream:
    try:
        result = process_frame(frame_id, frame)
    except FrameProcessingError:
        logger.exception("frame processing failed: id=%d", frame_id)
        continue
```

不要捕获 `KeyboardInterrupt`、`SystemExit` 后继续运行。底层也不应替顶层决定是否退出整个系统。

---

## 6. 日志配置

只在应用入口配置一次：

```python
import logging


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
```

模块中：

```python
logger = logging.getLogger(__name__)
```

不要在每个模块调用 `basicConfig()`。

---

## 7. 视觉日志记录什么

| 字段 | 用途 |
|---|---|
| `frame_id` | 定位具体帧 |
| `source` | 摄像头、OBS、视频文件 |
| `shape`、`dtype` | 定位输入契约问题 |
| `duration_ms` | 性能分析 |
| `detection_count` | 识别结果概览 |
| `stage` | capture、preprocess、detect、serialize |
| `error_type` | 错误统计 |

```python
logger.debug(
    "frame processed id=%d shape=%s detections=%d duration_ms=%.2f",
    frame_id,
    frame.shape,
    len(detections),
    duration_ms,
)
```

逐帧细节通常用 `DEBUG`；`INFO` 记录启动、模型加载和周期性汇总，避免日志本身成为性能问题。

---

## 8. 日志级别

```text
DEBUG    中间 shape、阈值、数量等诊断信息
INFO     正常里程碑和周期性吞吐量
WARNING  可恢复异常，例如短时无帧
ERROR    当前任务失败，但进程可能继续
CRITICAL 系统无法安全继续
```

记录异常堆栈：

```python
try:
    process_frame(frame_id, frame)
except FrameProcessingError:
    logger.exception("failed to process frame id=%d", frame_id)
```

---

## 9. 正确计时

```python
from time import perf_counter

start = perf_counter()
result = process(frame)
duration_ms = (perf_counter() - start) * 1000.0
```

使用 `perf_counter()` 测量耗时，并说明计时范围是否包括模型推理和序列化。

---

## 10. 生成器与惰性帧流

```python
from collections.abc import Iterator

import numpy as np


def generate_test_frames(
    count: int,
    height: int,
    width: int,
) -> Iterator[tuple[int, np.ndarray]]:
    for frame_id in range(count):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        yield frame_id, frame
```

消费：

```python
for frame_id, frame in generate_test_frames(100, 720, 1280):
    process_frame(frame_id, frame)
```

价值：

- 按需产生数据。
- 控制内存占用。
- 可以组合过滤与转换。
- 与摄像头、视频文件等来源匹配。

---

## 11. 理解 `yield`

```python
def demo():
    print("before 1")
    yield 1
    print("before 2")
    yield 2
    print("done")
```

调用 `demo()` 不会立刻执行函数体。每次请求下一个值时，从上次暂停位置继续。

注意：

- 生成器通常只能消费一次。
- 异常可能在迭代时才发生。
- 打开的文件或设备必须可靠关闭。
- 不要为了重复遍历而无意中把无限流转成列表。

---

## 12. 组合生成器流水线

```python
from collections.abc import Iterable, Iterator


def sample_frames(
    frames: Iterable[tuple[int, object]],
    every: int,
) -> Iterator[tuple[int, object]]:
    if every <= 0:
        raise ValueError("every must be positive")

    for frame_id, frame in frames:
        if frame_id % every == 0:
            yield frame_id, frame
```

```text
frame source
→ validate
→ sample
→ process
→ yield structured results
```

每一层只关心自己的职责，更容易测试和替换。

---

## 13. 资源管理

普通文件优先使用上下文管理器：

```python
from pathlib import Path


def read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as file:
        return [line.rstrip("\n") for line in file]
```

未来使用摄像头时需要确保释放：

```python
capture = open_capture()
try:
    for frame in read_frames(capture):
        process(frame)
finally:
    capture.release()
```

---

## 14. pytest 基础结构

```text
tests/
├── test_models.py
├── test_image_ops.py
└── test_pipeline.py
```

测试采用“准备、执行、断言”结构：

```python
def test_parse_score_accepts_text_number() -> None:
    raw = "0.75"
    result = parse_score(raw)
    assert result == 0.75
```

测试名称应说明输入和期望行为。

---

## 15. 异常测试

```python
import pytest


def test_parse_score_rejects_invalid_text() -> None:
    with pytest.raises(InvalidDetectionError, match="invalid confidence"):
        parse_score("not-a-number")
```

不要只断言“发生任意异常”；错误类型和关键信息都属于接口。

---

## 16. 参数化测试

```python
@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (0.5, 0.5),
        ("0.75", 0.75),
        (1, 1.0),
    ],
)
def test_parse_score(raw_value: object, expected: float) -> None:
    assert parse_score(raw_value) == expected
```

参数化适合多组边界值、错误 shape、dtype 和坐标转换案例。

---

## 17. fixture

```python
import numpy as np
import pytest


@pytest.fixture
def color_frame() -> np.ndarray:
    return np.zeros((720, 1280, 3), dtype=np.uint8)
```

fixture 适合共享有意义的准备逻辑，但不要把每个常量都做成 fixture。

---

## 18. 浮点与数组断言

```python
assert result == pytest.approx(0.3)
```

NumPy 数组：

```python
np.testing.assert_allclose(actual, expected)
np.testing.assert_array_equal(actual, expected)
```

同时测试输入没有被修改：

```python
original = frame.copy()
process_frame(0, frame)
np.testing.assert_array_equal(frame, original)
```

---

## 19. 测试日志

pytest 的 `caplog` 可以检查日志：

```python
import logging


def test_bad_frame_is_logged(caplog) -> None:
    with caplog.at_level(logging.ERROR):
        handle_bad_frame(frame_id=7)

    assert "frame" in caplog.text
    assert "7" in caplog.text
```

不要过度断言完整日志字符串，否则小幅格式调整会导致大量脆弱测试。

---

## 20. 今日综合任务：可观测帧处理器

定义：

```python
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ProcessingResult:
    frame_id: int
    detection_count: int
    duration_ms: float
```

```python
def process_stream(
    frames: Iterable[tuple[int, np.ndarray]],
    *,
    skip_invalid: bool = True,
) -> Iterator[ProcessingResult]:
    ...
```

要求：

1. 惰性消费帧，不把全部帧转成列表。
2. 每帧校验 shape 和 dtype。
3. 记录处理耗时。
4. 错误日志包含 `frame_id`、shape 和异常类型。
5. 按配置跳过错误帧或停止。
6. 使用自定义异常和异常链。
7. 不修改输入帧。
8. 至少编写 10 个 pytest 测试。

---

## 21. 测试清单

- 正常生成并处理 3 帧。
- 空输入不产生结果。
- 灰度图被拒绝。
- 错误 dtype 被拒绝。
- 错误日志包含帧编号。
- 跳过错误时后续帧继续。
- 停止模式抛出领域异常。
- 生成器只在迭代时执行。
- 处理耗时非负。
- 输入帧不被修改。

运行：

```powershell
uv run pytest -q
uv run ruff check .
uv run pyright
```

---

## 22. 今日验收清单

- [ ] 自定义异常按处理策略划分。
- [ ] 会使用 `raise ... from ...`。
- [ ] 不使用空的 `except Exception: pass`。
- [ ] 能解释日志级别。
- [ ] 日志包含定位帧问题的上下文。
- [ ] 会使用 `perf_counter()` 计时。
- [ ] 能写和组合生成器。
- [ ] 知道生成器异常发生在消费阶段。
- [ ] 会写异常测试、参数化测试和 fixture。
- [ ] 完成可观测帧处理器。

## 23. 今日最低交付物

```text
src/hoyo_vision/exceptions.py
src/hoyo_vision/frame_stream.py
src/hoyo_vision/logging_config.py
tests/test_frame_stream.py
```

建议提交信息：

```text
feat: add observable frame processing stream
```

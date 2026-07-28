# Day 8：检测框坐标系统

> 上一课：[Day 07：reshape、transpose 与模型输入布局](./Day07-reshape-transpose与模型输入布局.md)
> 下一课：[Day 09：IoU 与多帧稳定性](./Day09-IoU与多帧稳定性.md)

## 1. 今日目标

今天建立 YOLO 后处理最重要的数据基础：统一检测框坐标格式，并正确完成裁剪、归一化、缩放和格式转换。建议用时：**2～3 小时**。

完成后应能：

- 区分 `xyxy`、左上角 `xywh` 和中心点 `cxcywh`。
- 区分绝对像素坐标与归一化坐标。
- 批量裁剪、转换和缩放检测框。
- 正确处理 resize 和 letterbox 产生的坐标映射。
- 为所有 box 操作定义统一 shape、dtype 和边界规则。

---

## 2. 先统一术语

本项目建议使用以下命名：

```text
xyxy   = (x1, y1, x2, y2)
xywh   = (x, y, width, height)，x/y 表示左上角
cxcywh = (center_x, center_y, width, height)
```

很多框架把 `xywh` 用来表示中心点格式，也有框架表示左上角格式。函数名必须明确，不能只写含糊的 `convert_box()`。

推荐内部标准格式：

```text
float32 的绝对像素 xyxy，shape 为 (N, 4)
```

只在输入输出边界转换其他格式。

---

## 3. 坐标边界约定

本课程采用连续坐标理解：

```text
0 <= x1 <= x2 <= image_width
0 <= y1 <= y2 <= image_height
面积 = (x2 - x1) × (y2 - y1)
```

右下边界可以等于宽和高，正好适配 NumPy 的半开切片：

```python
image[y1:y2, x1:x2]
```

不要在不同模块中混用“右下角包含”与“不包含”的规则。

---

## 4. 输入校验

```python
import numpy as np


def as_boxes_xyxy(boxes: np.ndarray) -> np.ndarray:
    result = np.asarray(boxes, dtype=np.float32)
    if result.ndim != 2 or result.shape[1] != 4:
        raise ValueError(f"boxes must have shape (N, 4), got {result.shape}")
    if not np.isfinite(result).all():
        raise ValueError("box coordinates must be finite")
    return result
```

是否允许反向坐标要明确：

- 严格模型：直接拒绝 `x2 < x1` 或 `y2 < y1`。
- 宽容边界适配器：先纠正或裁剪，再记录 warning。

内部算法推荐严格，外部解析器负责兼容脏数据。

---

## 5. `xyxy` 转左上角 `xywh`

```python
def xyxy_to_xywh(boxes: np.ndarray) -> np.ndarray:
    source = as_boxes_xyxy(boxes)
    result = np.empty_like(source)
    result[:, 0] = source[:, 0]
    result[:, 1] = source[:, 1]
    result[:, 2] = source[:, 2] - source[:, 0]
    result[:, 3] = source[:, 3] - source[:, 1]
    return result
```

输入和输出均为 `(N, 4)`，不修改输入。

---

## 6. 左上角 `xywh` 转 `xyxy`

```python
def xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    source = np.asarray(boxes, dtype=np.float32)
    if source.ndim != 2 or source.shape[1] != 4:
        raise ValueError(f"boxes must have shape (N, 4), got {source.shape}")

    result = np.empty_like(source)
    result[:, 0] = source[:, 0]
    result[:, 1] = source[:, 1]
    result[:, 2] = source[:, 0] + source[:, 2]
    result[:, 3] = source[:, 1] + source[:, 3]
    return result
```

宽和高为负数时应拒绝，避免产生反向坐标。

---

## 7. `xyxy` 与 `cxcywh`

```python
def xyxy_to_cxcywh(boxes: np.ndarray) -> np.ndarray:
    source = as_boxes_xyxy(boxes)
    result = np.empty_like(source)

    result[:, 0] = (source[:, 0] + source[:, 2]) / 2.0
    result[:, 1] = (source[:, 1] + source[:, 3]) / 2.0
    result[:, 2] = source[:, 2] - source[:, 0]
    result[:, 3] = source[:, 3] - source[:, 1]
    return result
```

```python
def cxcywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    source = np.asarray(boxes, dtype=np.float32)
    if source.ndim != 2 or source.shape[1] != 4:
        raise ValueError(f"boxes must have shape (N, 4), got {source.shape}")

    half_width = source[:, 2] / 2.0
    half_height = source[:, 3] / 2.0

    result = np.empty_like(source)
    result[:, 0] = source[:, 0] - half_width
    result[:, 1] = source[:, 1] - half_height
    result[:, 2] = source[:, 0] + half_width
    result[:, 3] = source[:, 1] + half_height
    return result
```

---

## 8. 批量面积

```python
def box_area_xyxy(boxes: np.ndarray) -> np.ndarray:
    source = as_boxes_xyxy(boxes)
    widths = np.clip(source[:, 2] - source[:, 0], 0.0, None)
    heights = np.clip(source[:, 3] - source[:, 1], 0.0, None)
    return widths * heights
```

输出 shape `(N,)`。零面积框返回 0，不产生负面积。

---

## 9. 坐标裁剪

```python
def clip_boxes_xyxy(
    boxes: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    result = as_boxes_xyxy(boxes).copy()
    result[:, [0, 2]] = np.clip(result[:, [0, 2]], 0.0, float(width))
    result[:, [1, 3]] = np.clip(result[:, [1, 3]], 0.0, float(height))
    return result
```

裁剪后应重新检查面积，因为完全位于画面外的框可能变成零面积框。

---

## 10. 绝对坐标转归一化坐标

```python
def normalize_boxes_xyxy(
    boxes: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    result = as_boxes_xyxy(boxes).copy()
    scale = np.array([width, height, width, height], dtype=np.float32)
    result /= scale
    return result
```

归一化结果通常在 `[0,1]`，前提是输入已经裁剪到图像边界。

反归一化：

```python
def denormalize_boxes_xyxy(
    boxes: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    source = as_boxes_xyxy(boxes)
    scale = np.array([width, height, width, height], dtype=np.float32)
    return source * scale
```

---

## 11. 普通 resize 后的坐标缩放

原图尺寸 `(source_height, source_width)`，目标尺寸 `(target_height, target_width)`：

```python
def scale_boxes_xyxy(
    boxes: np.ndarray,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> np.ndarray:
    source_height, source_width = source_size
    target_height, target_width = target_size

    if min(source_height, source_width, target_height, target_width) <= 0:
        raise ValueError("all dimensions must be positive")

    scale_x = target_width / source_width
    scale_y = target_height / source_height
    scale = np.array([scale_x, scale_y, scale_x, scale_y], dtype=np.float32)
    return as_boxes_xyxy(boxes) * scale
```

非等比例 resize 时 `scale_x != scale_y`，框会随图像一起拉伸。

---

## 12. Letterbox 坐标映射

YOLO 预处理常保持宽高比，并在剩余区域填充。设：

```text
scale = min(target_width/source_width, target_height/source_height)
resized_width  = source_width × scale
resized_height = source_height × scale
pad_x = (target_width - resized_width) / 2
pad_y = (target_height - resized_height) / 2
```

原图框映射到 letterbox 图：

```text
x' = x × scale + pad_x
y' = y × scale + pad_y
```

模型输出映射回原图：

```text
x = (x' - pad_x) / scale
y = (y' - pad_y) / scale
```

最后裁剪到原图边界。

实现返回变换元数据：

```python
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class LetterboxTransform:
    scale: float
    pad_x: float
    pad_y: float
    source_size: tuple[int, int]
    target_size: tuple[int, int]
```

不要只保存 resize 后图像而丢失 scale 和 padding，否则模型框无法准确还原。

---

## 13. 映射 letterbox 框回原图

```python
def undo_letterbox_boxes(
    boxes: np.ndarray,
    transform: LetterboxTransform,
) -> np.ndarray:
    result = as_boxes_xyxy(boxes).copy()
    result[:, [0, 2]] -= transform.pad_x
    result[:, [1, 3]] -= transform.pad_y
    result /= np.float32(transform.scale)

    source_height, source_width = transform.source_size
    return clip_boxes_xyxy(result, source_width, source_height)
```

测试应使用手工可算的尺寸，例如 100×200 映射到 200×200，scale 为 1，垂直 padding 为 50。

---

## 14. 坐标取整只在边界发生

检测、缩放、IoU 和跟踪过程中保持 `float32`，不要过早转整数。只有真正用于图像切片或绘制像素时才取整：

```python
x1_int = int(np.floor(x1))
y1_int = int(np.floor(y1))
x2_int = int(np.ceil(x2))
y2_int = int(np.ceil(y2))
```

反复取整会积累误差，影响小目标和多次缩放。

---

## 15. 空数组是一等公民

所有批量函数都应支持：

```python
empty = np.empty((0, 4), dtype=np.float32)
```

预期：

```text
格式转换 → (0, 4)
面积计算 → (0,)
裁剪     → (0, 4)
缩放     → (0, 4)
```

“没有检测结果”是正常业务状态，不应被当作异常。

---

## 16. 常见错误

1. 将 `xywh` 错当成中心点格式。
2. 宽高顺序与图像 shape 的高宽顺序混淆。
3. 归一化 x 坐标除以 height。
4. resize 后只使用一个缩放系数处理非等比例缩放。
5. letterbox 忘记减 padding。
6. 过早把坐标转为整数。
7. 转换函数原地修改模型输出。
8. 空框数组 shape 写成 `(0,)`。

---

## 17. 今日综合任务：`box_ops.py`

实现并测试：

```python
as_boxes_xyxy
box_area_xyxy
clip_boxes_xyxy
xyxy_to_xywh
xywh_to_xyxy
xyxy_to_cxcywh
cxcywh_to_xyxy
normalize_boxes_xyxy
denormalize_boxes_xyxy
scale_boxes_xyxy
undo_letterbox_boxes
```

统一规则：

```text
输入 shape: (N, 4)
内部 dtype: float32
默认不修改输入
错误信息包含实际 shape
支持 (0, 4)
```

---

## 18. 建议测试

- 单框和多框格式往返。
- 空 `(0,4)` 输入。
- 零面积框。
- 反向坐标按契约拒绝。
- `NaN` 和无穷值拒绝。
- 越界裁剪。
- 绝对→归一化→绝对往返。
- 16:9 到 1:1 的非等比例缩放。
- letterbox 有水平 padding。
- letterbox 有垂直 padding。
- 输入数组不被修改。
- 输出 dtype 为 `float32`。

使用：

```python
np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
```

---

## 19. 今日验收清单

- [ ] 能解释三种框格式的差别。
- [ ] 明确内部标准格式和 dtype。
- [ ] 正确处理宽、高与 x、y 的对应关系。
- [ ] 能批量裁剪和计算面积。
- [ ] 能完成绝对与归一化坐标转换。
- [ ] 能完成普通 resize 坐标缩放。
- [ ] 能解释 letterbox 的 scale 和 padding。
- [ ] 坐标只在切片/绘制边界取整。
- [ ] 所有函数支持空框数组。
- [ ] 完成 `box_ops.py` 并覆盖边界测试。

## 20. 今日最低交付物

```text
src/hoyo_vision/box_ops.py
src/hoyo_vision/models.py
tests/test_box_ops.py
examples/day08_box_conversion_demo.py
```

建议提交信息：

```text
feat: add bounding box coordinate utilities
```

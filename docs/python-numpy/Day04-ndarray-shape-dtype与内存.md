# Day 4：ndarray、shape、dtype 与内存

> 上一课：[Day 03：异常、日志、生成器与 pytest](./Day03-异常日志生成器与pytest.md)
> 下一课：[Day 05：索引、切片、ROI、视图与副本](./Day05-索引切片ROI视图与副本.md)

## 1. 今日目标

今天开始 NumPy 核心。目标不是记 API，而是建立“数组数据契约”思维。建议用时：**2～3 小时**。

完成后应能：

- 解释 `shape`、`ndim`、`dtype`、`size`、`itemsize` 和 `nbytes`。
- 看懂灰度图、彩色图、批量图像和检测框的 shape。
- 估算视频帧占用内存。
- 识别 `uint8` 溢出和错误 dtype 转换。
- 理解 `strides`、C 连续和内存布局的基本含义。

---

## 2. ndarray 不只是更快的列表

NumPy 数组具有固定维度、统一 dtype 和规则内存布局，并支持底层批量运算：

```python
import numpy as np

image = np.zeros((720, 1280, 3), dtype=np.uint8)
```

这是一张 720 高、1280 宽、3 通道的图像。

---

## 3. 必须掌握的属性

```python
print(image.ndim)                 # 3
print(image.shape)                # (720, 1280, 3)
print(image.dtype)                # uint8
print(image.size)                 # 元素总数
print(image.itemsize)             # 每个元素字节数
print(image.nbytes)               # 数据区总字节数
print(image.strides)              # 每个轴移动一步跨过的字节
print(image.flags.c_contiguous)   # 是否 C 连续
```

关系：

```text
size = shape 各维度相乘
nbytes = size × itemsize
```

720p RGB `uint8` 图像约占：

```text
720 × 1280 × 3 × 1 byte = 2,764,800 bytes ≈ 2.64 MiB
```

100 帧约 264 MiB，因此视频通常逐帧或分批处理。

---

## 4. 常见视觉 shape

| 数据 | shape | 轴含义 |
|---|---|---|
| 灰度图 | `(H, W)` | 高、宽 |
| 彩色图 | `(H, W, C)` | 高、宽、通道 |
| 图像批次 | `(N, H, W, C)` | 批次、高、宽、通道 |
| 模型输入 | `(N, C, H, W)` | 批次、通道、高、宽 |
| 检测框 | `(M, 4)` | M 个 `x1,y1,x2,y2` |
| 批量检测框 | `(N, M, 4)` | 帧、框、坐标 |
| 分数/类别 | `(N, M)` | 帧、框 |
| IoU 矩阵 | `(A, B)` | 两组框两两比较 |

每个不直观操作前写出契约：

```python
# frames: (N, H, W, C), uint8
# means: (N, C)
means = frames.mean(axis=(1, 2))
```

---

## 5. 创建数组

```python
zeros = np.zeros((2, 3), dtype=np.float32)
ones = np.ones((2, 3), dtype=np.float32)
filled = np.full((2, 3), 255, dtype=np.uint8)
identity = np.eye(3, dtype=np.float32)
values = np.arange(0, 10, 2)
steps = np.linspace(0.0, 1.0, 5, dtype=np.float32)
```

检测框：

```python
boxes = np.array(
    [
        [10, 20, 100, 200],
        [50, 60, 150, 180],
    ],
    dtype=np.float32,
)
```

测试数据和模型接口数据尽量显式指定 dtype。

---

## 6. `array`、`asarray` 和复制

```python
result = np.asarray(source, dtype=np.float32)
```

输入已经符合要求时，`asarray` 可能不复制；`np.array` 默认更倾向于创建新数组。必要时检查：

```python
np.shares_memory(source, result)
```

不要仅凭函数名猜测所有权，Day 5 会深入视图与副本。

---

## 7. 常见 dtype

| dtype | 用途 |
|---|---|
| `uint8` | 0～255 普通图像 |
| `uint16` | 深度图、高位深图像 |
| `int32` / `int64` | 类别 ID、索引 |
| `float32` | 模型输入、坐标、置信度 |
| `float64` | NumPy 默认浮点，推理通常不需要 |
| `bool` | 掩码 |

转换：

```python
image_float = image.astype(np.float32)
```

默认产生新数组。如果 dtype 已相同，可以研究 `copy=False`，但仍需理解共享关系。

---

## 8. `uint8` 溢出

```python
pixels = np.array([250], dtype=np.uint8)
print(pixels + 10)
```

`uint8` 无法表示 260。亮度、差值和归一化计算应先转换：

```python
working = pixels.astype(np.float32)
brightened = np.clip(working + 10.0, 0.0, 255.0).astype(np.uint8)
```

减法也有风险：

```python
left = np.array([5], dtype=np.uint8)
right = np.array([10], dtype=np.uint8)
```

计算差值前转成有符号或浮点类型。

---

## 9. 归一化

```python
def normalize_uint8_image(image: np.ndarray) -> np.ndarray:
    return image.astype(np.float32) / np.float32(255.0)
```

数据契约：

```text
输入 shape: 不变
输入 dtype: uint8
输出 dtype: float32
输出范围: [0, 1]
修改输入: 否
```

测试数值、dtype 和输入不变：

```python
image = np.array([0, 127, 255], dtype=np.uint8)
original = image.copy()
result = normalize_uint8_image(image)

assert result.dtype == np.float32
np.testing.assert_allclose(result, [0.0, 127 / 255, 1.0])
np.testing.assert_array_equal(image, original)
```

---

## 10. dtype 提升需要实测

混合 dtype 运算可能发生类型提升：

```python
result = uint8_array + float_array
print(result.dtype)
```

不要假设所有结果自然成为 `float32`。模型输入通常要求明确的 `float32`，必要时控制输入数组、常量和输出类型。

---

## 11. `strides` 的直觉

C 连续的 `(H, W, C)` `uint8` 图像：

```text
通道轴移动 1 步：1 byte
宽度轴移动 1 步：C bytes
高度轴移动 1 步：W × C bytes
```

```python
image = np.zeros((720, 1280, 3), dtype=np.uint8)
print(image.strides)  # 常见结果：(3840, 3, 1)
```

这表示向下移动一行跨 3840 字节，向右移动一个像素跨 3 字节。

---

## 12. 连续内存

```python
print(image.flags.c_contiguous)
```

切片和转置结果可能不是连续的。NumPy 可以处理，但部分模型或底层接口要求连续内存：

```python
contiguous = np.ascontiguousarray(array)
```

不要对每个数组无脑调用，否则可能造成不必要复制。只在接口要求或测试证明必要时使用。

---

## 13. `len`、`size` 与空数组

```python
len(image)
```

只返回第一个轴长度 H，不是像素总数。元素总数用 `image.size`，完整结构用 `image.shape`。

零个检测框仍应保留二维契约：

```python
empty_boxes = np.empty((0, 4), dtype=np.float32)
```

不要使用 shape `(0,)`，否则下游的 `boxes[:, :2]` 会失败。

---

## 14. 图像校验

```python
class InvalidImageError(ValueError):
    pass


def validate_color_image(image: np.ndarray) -> None:
    if not isinstance(image, np.ndarray):
        raise InvalidImageError("image must be a numpy.ndarray")
    if image.ndim != 3:
        raise InvalidImageError(f"expected 3 dimensions, got {image.ndim}")
    if image.shape[2] != 3:
        raise InvalidImageError(f"expected 3 channels, got {image.shape}")
    if image.shape[0] <= 0 or image.shape[1] <= 0:
        raise InvalidImageError(f"height and width must be positive: {image.shape}")
    if image.dtype != np.uint8:
        raise InvalidImageError(f"expected uint8, got {image.dtype}")
```

校验顺序应保证错误信息本身不会再次报错。

---

## 15. 数组描述器

```python
def describe_array(
    array: np.ndarray,
    *,
    include_stats: bool = False,
) -> dict[str, object]:
    description: dict[str, object] = {
        "shape": array.shape,
        "ndim": array.ndim,
        "dtype": str(array.dtype),
        "size": array.size,
        "itemsize": array.itemsize,
        "nbytes": array.nbytes,
        "strides": array.strides,
        "c_contiguous": array.flags.c_contiguous,
        "writeable": array.flags.writeable,
    }

    if include_stats:
        description["min"] = array.min().item() if array.size else None
        description["max"] = array.max().item() if array.size else None

    return description
```

对大数组求 min/max 会扫描全部数据，因此通过参数显式控制昂贵统计。

---

## 16. 内存估算练习

计算：

1. 一张 1920×1080 RGB `uint8` 图像。
2. 同一图像转成 `float32`。
3. 60 张 1080p `uint8` 图像。
4. `(16, 3, 640, 640)` `float32` 模型输入。

公式：

```text
shape 各维度乘积 × dtype 字节数
```

结论：不要把长视频一次性转成 `float32` 批次；使用生成器、有界队列或小批处理。

---

## 17. 可复现随机数据

```python
rng = np.random.default_rng(seed=42)
image = rng.integers(
    0,
    256,
    size=(720, 1280, 3),
    dtype=np.uint8,
)
```

固定 seed 让实验可复现，但关键边界值仍应显式构造。

---

## 18. 今日综合任务

实现：

```python
def describe_array(array: np.ndarray, *, include_stats: bool = False) -> dict[str, object]:
    ...


def validate_color_image(image: np.ndarray) -> None:
    ...


def normalize_uint8_image(image: np.ndarray) -> np.ndarray:
    ...
```

要求：

- 异常包含实际 shape 和 dtype。
- 空数组统计不报错。
- 浮点数组可报告 `NaN` 和无穷值。
- 归一化输出必须是 `float32`。
- 输入数组不被修改。
- `nbytes == size * itemsize`。

---

## 19. 建议测试

- `(720, 1280, 3)` `uint8` 正常通过。
- 灰度图和四通道图被拒绝。
- 高或宽为 0 被拒绝。
- `float32` 图像按契约拒绝。
- 0、127、255 归一化正确。
- 输出 dtype 为 `float32`。
- 原输入数值与 dtype 不变。
- 空数组描述器不执行非法 min/max。
- 浮点数组正确识别 `NaN`。

---

## 20. 今日验收清单

- [ ] 能解释 shape 中每个轴的业务含义。
- [ ] 能区分 `ndim`、`size` 和 `len(array)`。
- [ ] 能估算图像批次内存。
- [ ] 知道常见视觉 dtype 的用途。
- [ ] 能解释 `uint8` 加减法风险。
- [ ] 归一化时显式得到 `float32`。
- [ ] 能解释 `strides` 的基本含义。
- [ ] 知道连续数组和非连续数组的区别。
- [ ] 能表示 `(0, 4)` 的空检测框数组。
- [ ] 完成数组描述器、校验器和归一化函数。

## 21. 今日最低交付物

```text
src/hoyo_vision/image_ops.py
tests/test_image_ops.py
examples/day04_array_inspector.py
```

建议提交信息：

```text
feat: add ndarray inspection and image validation
```

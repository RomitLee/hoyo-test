# Day 6：broadcasting、axis 与向量化

> 上一课：[Day 05：索引、切片、ROI、视图与副本](./Day05-索引切片ROI视图与副本.md)
> 下一课：[Day 07：reshape、transpose 与模型输入布局](./Day07-reshape-transpose与模型输入布局.md)

## 1. 今日目标

今天建立 NumPy 最关键的计算思维：用轴、广播和掩码批量处理数据，而不是逐像素写 Python 循环。建议用时：**2～3 小时**。

完成后应能：

- 判断两个 shape 是否可以广播。
- 正确选择归约操作的 `axis`。
- 使用 `keepdims=True` 保持广播友好的 shape。
- 使用布尔掩码、`np.where`、`clip` 完成批量处理。
- 批量计算图像通道统计和检测框面积。
- 编写向量化与循环版本的性能对比。

---

## 2. 为什么需要向量化

不推荐逐像素处理：

```python
for y in range(image.shape[0]):
    for x in range(image.shape[1]):
        for c in range(image.shape[2]):
            image[y, x, c] = image[y, x, c] / 255.0
```

推荐：

```python
normalized = image.astype(np.float32) / np.float32(255.0)
```

NumPy 把循环交给底层实现，通常更快、更简洁，也更容易看出数据公式。

合理的 Python 循环包括：

- 逐帧读取视频。
- 维护状态机。
- 处理数量很少、逻辑复杂的业务对象。

应重点避免像素级和大量检测框上的双层 Python 循环。

---

## 3. broadcasting 规则

从 shape 尾部开始对齐，每一维满足以下任一条件即可广播：

1. 两个维度相等。
2. 其中一个维度为 1。
3. 某个数组缺少该维度，可视为 1。

示例：

```text
(H, W, 3)
      (3,)
-----------
(H, W, 3)
```

```python
mean = np.array([123.0, 117.0, 104.0], dtype=np.float32)
result = image.astype(np.float32) - mean
```

不能广播：

```text
(H, W, 3)
      (4,)
```

---

## 4. 用小数组验证广播

```python
image = np.arange(24, dtype=np.float32).reshape(2, 4, 3)
offset = np.array([10, 20, 30], dtype=np.float32)
result = image + offset
```

先预测：

```text
image:  (2, 4, 3)
offset:       (3,)
result: (2, 4, 3)
```

学习广播时先用尺寸很小的数据打印完整结果，确认每个通道发生了什么。

---

## 5. 主动增加维度

```python
scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)  # (N,)
column = scores[:, None]                               # (N, 1)
row = scores[None, :]                                  # (1, N)
```

这在两两计算中非常重要：

```text
boxes_a[:, None, :] → (N, 1, 4)
boxes_b[None, :, :] → (1, M, 4)
广播结果             → (N, M, 4)
```

Day 9 的 IoU 矩阵将使用这一技巧。

---

## 6. `axis` 的真正含义

`axis` 指定被归约或操作的轴。

对 `(H, W, C)` 图像：

```python
image.mean(axis=0)       # 消除 H，结果 (W, C)
image.mean(axis=1)       # 消除 W，结果 (H, C)
image.mean(axis=2)       # 消除 C，结果 (H, W)
image.mean(axis=(0, 1))  # 消除 H、W，结果 (C,)
```

对 `(N, H, W, C)` 批次：

```python
frames.mean(axis=(1, 2))    # 每帧每通道均值：(N, C)
frames.mean(axis=(0, 1, 2)) # 整批每通道均值：(C,)
```

不要背数字，先写每个轴的业务含义，再决定要消除哪些轴。

---

## 7. `keepdims=True`

```python
channel_mean = frames.mean(axis=(1, 2), keepdims=True)
```

输入 `(N, H, W, C)`，输出 `(N, 1, 1, C)`。这样可以直接广播回原批次：

```python
centered = frames - channel_mean
```

如果不用 `keepdims`，结果为 `(N, C)`，不能直接与 `(N, H, W, C)` 按期望广播。

---

## 8. 常见归约操作

```python
array.sum(axis=...)
array.mean(axis=...)
array.min(axis=...)
array.max(axis=...)
array.std(axis=...)
array.any(axis=...)
array.all(axis=...)
```

示例：每个检测框坐标是否全部有限：

```python
finite_per_box = np.isfinite(boxes).all(axis=1)  # (N,)
```

每帧是否存在高置信度目标：

```python
has_high_score = (scores >= 0.8).any(axis=1)  # (N,)
```

---

## 9. 使用掩码批量过滤

```python
widths = boxes[:, 2] - boxes[:, 0]
heights = boxes[:, 3] - boxes[:, 1]
areas = np.clip(widths, 0.0, None) * np.clip(heights, 0.0, None)

mask = (scores >= 0.5) & (areas >= 16.0)
kept_boxes = boxes[mask]
kept_scores = scores[mask]
```

布尔运算使用：

```text
& 逐元素且
| 逐元素或
~ 逐元素非
```

每个条件都加括号。不要对数组使用 Python 的 `and` / `or`。

---

## 10. `np.where`

二值化：

```python
binary = np.where(gray >= 128, 255, 0).astype(np.uint8)
```

根据分数选择标签：

```python
states = np.where(scores >= threshold, "keep", "drop")
```

只需要布尔掩码时直接写比较表达式，不必再使用 `where`：

```python
mask = scores >= threshold
```

---

## 11. 通道归一化

```python
def standardize_image(
    image: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray,
) -> np.ndarray:
    working = image.astype(np.float32) / np.float32(255.0)
    mean = np.asarray(mean, dtype=np.float32)
    std = np.asarray(std, dtype=np.float32)

    if mean.shape != (3,) or std.shape != (3,):
        raise ValueError("mean and std must have shape (3,)")
    if np.any(std <= 0):
        raise ValueError("std values must be positive")

    return (working - mean) / std
```

广播关系：

```text
working: (H, W, 3)
mean:          (3,)
std:           (3,)
output:  (H, W, 3)
```

---

## 12. 批量检测框面积

```python
def box_area_xyxy(boxes: np.ndarray) -> np.ndarray:
    boxes = np.asarray(boxes, dtype=np.float32)
    if boxes.ndim != 2 or boxes.shape[1] != 4:
        raise ValueError(f"boxes must have shape (N, 4), got {boxes.shape}")

    widths = np.clip(boxes[:, 2] - boxes[:, 0], 0.0, None)
    heights = np.clip(boxes[:, 3] - boxes[:, 1], 0.0, None)
    return widths * heights
```

输入 `(N, 4)`，输出 `(N,)`。支持 `(0, 4)` 时自然返回 `(0,)`。

---

## 13. 批量裁剪检测框

```python
def clip_boxes_xyxy(
    boxes: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    result = np.asarray(boxes, dtype=np.float32).copy()
    if result.ndim != 2 or result.shape[1] != 4:
        raise ValueError(f"boxes must have shape (N, 4), got {result.shape}")

    result[:, [0, 2]] = np.clip(result[:, [0, 2]], 0.0, width)
    result[:, [1, 3]] = np.clip(result[:, [1, 3]], 0.0, height)
    return result
```

这里没有逐框循环。返回副本，输入不被修改。

---

## 14. 批次图像统计

```python
# frames: (N, H, W, C), uint8
working = frames.astype(np.float32)

per_frame_channel_mean = working.mean(axis=(1, 2))       # (N, C)
per_frame_brightness = working.mean(axis=(1, 2, 3))      # (N,)
global_channel_mean = working.mean(axis=(0, 1, 2))       # (C,)
```

每一步先预测 shape，再运行断言：

```python
assert per_frame_channel_mean.shape == (frames.shape[0], frames.shape[3])
```

---

## 15. 避免巨大中间数组

广播本身不一定复制数据，但表达式的计算结果可能创建巨大中间数组：

```python
normalized = (frames.astype(np.float32) - mean) / std
```

对大批次可能同时存在：

- 原始 `uint8`。
- `astype` 后的 `float32`。
- 减法中间结果。
- 最终除法结果。

优化顺序：

1. 先保证正确。
2. 测量真实峰值内存和耗时。
3. 减小批次。
4. 复用确实拥有的工作数组。
5. 不要过早写难读的原地运算。

---

## 16. 性能基准

```python
from time import perf_counter


def benchmark(function, *args, repeat: int = 10) -> float:
    start = perf_counter()
    for _ in range(repeat):
        function(*args)
    return (perf_counter() - start) / repeat
```

比较：

- Python 双层循环归一化。
- NumPy 向量化归一化。
- 不同图像尺寸和批次数量。

基准应记录：

```text
输入 shape、dtype、重复次数、平均耗时、加速倍数
```

不要只运行一次，也不要把数据创建时间混入核心函数计时。

---

## 17. 今日综合任务：批量视觉统计器

实现：

```python
def analyze_batch(
    frames: np.ndarray,
    boxes: np.ndarray,
    scores: np.ndarray,
    *,
    confidence_threshold: float = 0.5,
    minimum_area: float = 16.0,
) -> dict[str, np.ndarray]:
    ...
```

约定：

```text
frames: (N, H, W, C)
boxes:  (K, 4)
scores: (K,)
```

返回：

- 每帧每通道均值 `(N, C)`。
- 每帧整体亮度 `(N,)`。
- 每个框面积 `(K,)`。
- 有效框掩码 `(K,)`。
- 过滤后的框和分数。

要求：

- 不使用像素级循环。
- 不使用检测框级循环。
- 输入不被修改。
- 输出 shape 和 dtype 有测试。
- 支持空框数组。

---

## 18. 建议测试

- 手工可计算的 2×2×3 图像。
- 单帧和多帧批次。
- `keepdims` 结果能广播回原数组。
- 空 `(0, 4)` 检测框。
- 零面积和负面积框。
- 分数恰好等于阈值。
- `std` 包含 0 时拒绝。
- 向量化结果与参考循环结果一致。
- 输入数组保持不变。

---

## 19. 今日验收清单

- [ ] 能从尾部判断广播兼容性。
- [ ] 会使用 `None`/`np.newaxis` 增加维度。
- [ ] 能根据业务语义选择 `axis`。
- [ ] 会使用 `keepdims=True` 保持广播能力。
- [ ] 能使用 `any`、`all` 做按轴判断。
- [ ] 会使用布尔掩码、`np.where` 和 `np.clip`。
- [ ] 不对像素和大量框写 Python 双层循环。
- [ ] 能批量计算框面积和图像统计。
- [ ] 知道广播表达式可能创建大型中间结果。
- [ ] 完成向量化与循环版本的基准比较。

## 20. 今日最低交付物

```text
src/hoyo_vision/image_ops.py
src/hoyo_vision/box_ops.py
tests/test_vectorization.py
benchmarks/vectorization_benchmark.py
```

建议提交信息：

```text
feat: add vectorized image and box operations
```

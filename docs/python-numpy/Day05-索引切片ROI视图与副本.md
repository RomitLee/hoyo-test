# Day 5：索引、切片、ROI、视图与副本

> 上一课：[Day 04：ndarray、shape、dtype 与内存](./Day04-ndarray-shape-dtype与内存.md)
> 下一课：[Day 06：broadcasting、axis 与向量化](./Day06-broadcasting-axis与向量化.md)

## 1. 今日目标

今天解决视觉项目中非常高频的错误：坐标顺序混淆、ROI 越界、切片意外修改原图，以及高级索引产生隐藏副本。建议用时：**2～3 小时**。

完成后应能：

- 使用基础索引、切片、省略号和布尔索引。
- 理解基础切片通常返回视图，高级索引通常返回副本。
- 使用 `np.shares_memory` 验证共享关系。
- 正确处理 `(x1, y1, x2, y2)` 与图像 `[y1:y2, x1:x2]`。
- 实现安全的单个和批量 ROI 裁剪函数。

---

## 2. 图像索引顺序

检测框常写作：

```text
(x1, y1, x2, y2)
```

NumPy 图像索引：

```python
roi = image[y1:y2, x1:x2]
```

数组轴：

```text
axis 0 → 高度 y
axis 1 → 宽度 x
axis 2 → 通道 c
```

请形成条件反射：

```text
坐标表达：x 在前，y 在后
数组索引：y 在前，x 在后
```

---

## 3. 基础索引

```python
pixel = image[y, x]       # shape: (C,)
channel = image[y, x, c]  # 标量
row = image[y]            # shape: (W, C)
```

负索引从末尾开始。视觉业务代码中，检测框负值通常代表越界，应显式裁剪，不要让 NumPy 把它解释为从末尾计数。

---

## 4. 切片规则

```python
array[start:stop:step]
```

`stop` 不包含在结果中：

```python
roi = image[10:20, 30:50]
# ROI 高度 10，宽度 20
```

常见写法：

```python
image[:, :, 0]       # 第 0 通道，shape (H, W)
image[..., 0]        # 同上
image[::2, ::2]      # 高宽每隔一个像素采样
image[:, :, ::-1]    # 反转通道顺序
```

通道反转可能产生负 stride，某些底层库不接受，需要复制或转为连续数组。

---

## 5. 基础切片通常返回视图

```python
import numpy as np

image = np.zeros((100, 200, 3), dtype=np.uint8)
roi = image[10:30, 20:60]

print(np.shares_memory(image, roi))  # 通常 True
roi[:] = 255
print(image[10, 20])                 # 原图也改变
```

视图优点：创建快、节省内存。风险：修改 ROI 会污染原图，且非连续布局可能不符合下游接口。

---

## 6. 何时使用 `.copy()`

ROI 将被修改、缓存、跨线程传递或长期保存时，通常应复制：

```python
roi = image[y1:y2, x1:x2].copy()
```

只立即读取时可以保留视图。不要形成“所有切片都复制”的习惯，复制大区域会消耗时间和内存。

---

## 7. `.view()` 不等于复制

```python
view = image.view()

print(view is image)                  # False
print(np.shares_memory(view, image))  # True
```

`.view()` 创建新数组对象，但共享数据缓冲区。独立数据使用 `.copy()`。

---

## 8. 高级索引

整数数组索引：

```python
selected_rows = image[[0, 2, 4]]
```

布尔索引：

```python
scores = np.array([0.9, 0.2, 0.8], dtype=np.float32)
boxes = np.array(
    [[0, 0, 10, 10], [5, 5, 8, 8], [20, 20, 40, 40]],
    dtype=np.float32,
)

mask = scores >= 0.5
kept_boxes = boxes[mask]
```

高级索引通常创建副本：

```python
print(np.shares_memory(boxes, kept_boxes))  # 通常 False
```

高级索引 shape 规则复杂时，先用小数组实验，不要直接在真实大图上猜。

---

## 9. 布尔掩码

```python
gray = np.array([[10, 200], [150, 30]], dtype=np.uint8)
mask = gray >= 128
```

`mask` 与 `gray` shape 相同，dtype 为 `bool`。

读取满足条件的元素：

```python
bright_pixels = gray[mask]  # 通常变成一维
```

原地修改：

```python
gray[mask] = 255
```

保留输入：

```python
result = gray.copy()
result[mask] = 255
```

---

## 10. 检测框过滤必须同步

```python
mask = scores >= threshold
kept_boxes = boxes[mask]
kept_scores = scores[mask]
kept_class_ids = class_ids[mask]
```

三组数据必须用同一个掩码，否则框、分数和类别会错位。过滤前先校验：

```python
if not (len(boxes) == len(scores) == len(class_ids)):
    raise ValueError("boxes, scores and class_ids must have equal length")
```

---

## 11. 坐标转整数

检测框常为浮点数，切片需要整数。推荐策略：

```python
x1 = int(np.floor(box[0]))
y1 = int(np.floor(box[1]))
x2 = int(np.ceil(box[2]))
y2 = int(np.ceil(box[3]))
```

这样倾向于完整覆盖预测区域。推荐流程：

```text
校验有限数值
→ 裁剪浮点坐标
→ 左上 floor、右下 ceil
→ 再限制整数边界
→ 检查正面积
→ 切片
```

---

## 12. 裁剪单个框

```python
def clip_box_xyxy(
    box: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    result = np.asarray(box, dtype=np.float32).copy()
    if result.shape != (4,):
        raise ValueError(f"box must have shape (4,), got {result.shape}")
    if not np.isfinite(result).all():
        raise ValueError("box coordinates must be finite")

    result[0] = np.clip(result[0], 0.0, width)
    result[2] = np.clip(result[2], 0.0, width)
    result[1] = np.clip(result[1], 0.0, height)
    result[3] = np.clip(result[3], 0.0, height)
    return result
```

输入先复制，确保函数不修改调用方数组。

---

## 13. 安全 ROI 接口

```python
def crop_roi(
    image: np.ndarray,
    box: np.ndarray,
    *,
    copy: bool = True,
) -> np.ndarray:
    """按 xyxy 坐标裁剪 ROI。"""
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a numpy.ndarray")
    if image.ndim not in (2, 3):
        raise ValueError(f"expected 2D or 3D image, got {image.shape}")

    values = np.asarray(box, dtype=np.float32)
    if values.shape != (4,):
        raise ValueError(f"box must have shape (4,), got {values.shape}")
    if not np.isfinite(values).all():
        raise ValueError("box coordinates must be finite")

    height, width = image.shape[:2]
    x1, y1, x2, y2 = values

    x1 = int(np.floor(np.clip(x1, 0, width)))
    y1 = int(np.floor(np.clip(y1, 0, height)))
    x2 = int(np.ceil(np.clip(x2, 0, width)))
    y2 = int(np.ceil(np.clip(y2, 0, height)))

    if x2 <= x1 or y2 <= y1:
        raise ValueError(
            f"box has no positive area after clipping: {(x1, y1, x2, y2)}"
        )

    roi = image[y1:y2, x1:x2]
    return roi.copy() if copy else roi
```

数据契约：

```text
image: (H, W, C) 或 (H, W)
box: (4,), xyxy
输出: 非空 ROI
copy=True: 不共享原图内存
copy=False: 可以返回视图
```

---

## 14. 空 ROI

NumPy 对空切片不会自动报错：

```python
empty = image[20:20, 10:30]
print(empty.shape)  # (0, 20, C)
```

空 ROI 传给 OCR 或 resize 时会在更深层失败。因此应在裁剪边界立即检查，并在错误中包含原始框和裁剪后框。

---

## 15. 只读视图

```python
roi = image[y1:y2, x1:x2].view()
roi.flags.writeable = False
```

这只能防止通过该数组对象写入，不等于线程安全。工程上仍应通过所有权和接口设计控制修改行为。

---

## 16. `reshape` 也可能共享内存

```python
flat = image.reshape(-1)
```

内存布局允许时可能返回视图，不允许时可能复制。不要仅凭操作名称判断；用 `np.shares_memory` 验证。Day 7 会深入形状和轴转换。

---

## 17. 今日综合任务：ROI 批处理器

```python
def crop_many_rois(
    image: np.ndarray,
    boxes: np.ndarray,
    *,
    copy: bool = True,
    skip_invalid: bool = False,
) -> list[np.ndarray]:
    ...
```

要求：

1. `boxes` 必须是 `(N, 4)`。
2. 支持 `(0, 4)` 空输入。
3. 坐标必须有限。
4. 越界框自动裁剪。
5. 零面积框按配置抛错或跳过。
6. 输出顺序与输入框一致。
7. `copy=True` 时不共享原图内存。
8. 输入图像和框数组不被修改。
9. 错误信息包含框索引。

不同 ROI 尺寸可能不同，应返回 `list[np.ndarray]`，统一 resize 后才能 `stack`。

---

## 18. 建议测试

- 框完全位于图像内。
- 左上角负坐标。
- 右下角越界。
- 完全位于图像外。
- `x2 == x1` 或 `y2 < y1`。
- 含 `NaN` 或无穷值。
- 灰度图和彩色图。
- 空 `(0, 4)` 框数组。
- `copy=True` 不共享内存。
- `copy=False` 共享内存。
- 修改复制 ROI 不影响原图。
- 修改视图 ROI 能观察到原图变化。

---

## 19. 性能实验

创建一张 1080p 图像，裁剪同一区域 10,000 次，比较返回视图和返回副本：

- 总耗时。
- ROI 大小。
- 是否共享内存。
- 复制数据总量估算。

目标不是得出“永远不用 copy”，而是理解安全性、生命周期和性能之间的取舍。

---

## 20. 今日验收清单

- [ ] 牢记图像索引是 `[y, x]`。
- [ ] 理解切片 stop 不包含在结果中。
- [ ] 会使用省略号和通道索引。
- [ ] 知道基础切片通常返回视图。
- [ ] 知道高级索引通常返回副本。
- [ ] 会使用 `np.shares_memory` 验证。
- [ ] 能解释 `.view()` 与 `.copy()` 的区别。
- [ ] 能同步过滤框、分数和类别。
- [ ] 能处理负坐标、越界和空 ROI。
- [ ] 完成单个和批量 ROI 裁剪器。

## 21. 今日最低交付物

```text
src/hoyo_vision/image_ops.py
tests/test_image_ops.py
examples/day05_roi_demo.py
```

建议提交信息：

```text
feat: add safe ROI cropping utilities
```

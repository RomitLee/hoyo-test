# Day 7：reshape、transpose 与模型输入布局

> 上一课：[Day 06：broadcasting、axis 与向量化](./Day06-broadcasting-axis与向量化.md)
> 下一课：[Day 08：检测框坐标系统](./Day08-检测框坐标系统.md)

## 1. 今日目标

今天掌握视觉数据在 OpenCV 风格布局与深度学习模型布局之间的转换。建议用时：**2～3 小时**。

完成后应能：

- 区分 `reshape` 与 `transpose`。
- 正确完成 HWC↔CHW、NHWC↔NCHW。
- 使用 `moveaxis`、`expand_dims`、`squeeze`。
- 理解 `ravel`、`flatten` 和连续内存。
- 正确选择 `stack` 与 `concatenate`。
- 构建一个纯 NumPy 模型输入预处理器。

---

## 2. 视觉布局

OpenCV 和普通图像数组常使用：

```text
HWC = (height, width, channels)
NHWC = (batch, height, width, channels)
```

深度学习框架常使用：

```text
CHW = (channels, height, width)
NCHW = (batch, channels, height, width)
```

布局不是装饰信息。轴顺序错误时，程序可能不报错，但模型输出完全错误。

---

## 3. `reshape` 改变形状解释

```python
array = np.arange(24).reshape(2, 3, 4)
```

`reshape` 改变维度组合，通常保持元素逻辑顺序。常见用途：

- 展平批次。
- 恢复规则结构。
- 在总元素数不变时组合或拆分轴。

```python
flat = array.reshape(-1)
restored = flat.reshape(2, 3, 4)
```

`-1` 只能出现一次，由 NumPy 推断该维度。

---

## 4. `reshape` 不能代替 `transpose`

错误的 HWC→CHW：

```python
chw = image.reshape(3, height, width)
```

这只是重新解释线性元素顺序，会把像素和通道数据打乱。

正确做法：

```python
chw = image.transpose(2, 0, 1)
```

```text
原轴：0=H, 1=W, 2=C
新轴顺序：2, 0, 1
结果：C, H, W
```

---

## 5. 批次 NHWC→NCHW

```python
# frames: (N, H, W, C)
nchw = frames.transpose(0, 3, 1, 2)
```

轴映射：

```text
旧 0 N → 新 0
旧 3 C → 新 1
旧 1 H → 新 2
旧 2 W → 新 3
```

恢复：

```python
nhwc = nchw.transpose(0, 2, 3, 1)
```

测试 round-trip：

```python
np.testing.assert_array_equal(nhwc, frames)
```

---

## 6. `moveaxis`

当只想表达“把通道轴移动到某处”时，`moveaxis` 更直观：

```python
chw = np.moveaxis(image, -1, 0)
nchw = np.moveaxis(frames, -1, 1)
```

恢复：

```python
image = np.moveaxis(chw, 0, -1)
frames = np.moveaxis(nchw, 1, -1)
```

`transpose` 适合明确给出全部轴顺序；`moveaxis` 适合移动一个或少数轴。

---

## 7. 增加和删除批次轴

单张 CHW 图像增加批次轴：

```python
batch = chw[None, ...]
# 或
batch = np.expand_dims(chw, axis=0)
```

结果 `(1, C, H, W)`。

删除明确为 1 的轴：

```python
single = np.squeeze(batch, axis=0)
```

不要无参数调用 `squeeze()` 处理协议数据，它会删除所有长度为 1 的轴，可能意外删除高度、宽度或通道轴。

---

## 8. `ravel` 与 `flatten`

```python
raveled = image.ravel()
flattened = image.flatten()
```

- `ravel()` 尽量返回视图，必要时复制。
- `flatten()` 总是返回副本。

如果要修改结果并确保不影响原数组，使用 `flatten()` 或显式 `.copy()`。视觉流水线中通常不需要随意展平图像，除非接口明确要求。

---

## 9. 转置后的连续性

```python
chw = image.transpose(2, 0, 1)
print(chw.flags.c_contiguous)  # 通常 False
```

某些推理接口要求连续内存：

```python
chw = np.ascontiguousarray(chw)
```

推荐模型输入步骤：

```text
校验 HWC
→ 转 float32 并归一化
→ HWC 转 CHW
→ 增加批次轴
→ 按接口要求转连续
```

连续化应放在布局变换后，否则转置会再次产生非连续视图。

---

## 10. `stack` 创建新轴

```python
batch = np.stack([image1, image2, image3], axis=0)
```

如果每张图都是 `(H, W, C)`，结果是 `(N, H, W, C)`。所有输入 shape 必须一致。

常见用途：

- 多张 resize 后图像组成批次。
- 多帧同尺寸数据组成时间批次。

---

## 11. `concatenate` 沿已有轴连接

```python
combined = np.concatenate([batch_a, batch_b], axis=0)
```

如果两个批次分别是 `(N1, H, W, C)` 与 `(N2, H, W, C)`，结果为 `(N1+N2, H, W, C)`。

区别：

```text
stack       → 新增一个轴
concatenate → 沿现有轴连接
```

先问自己：“我要新增维度，还是延长已有维度？”

---

## 12. `vstack`、`hstack` 不适合作为主接口

这些快捷函数在不同维度下行为不够直观。视觉项目中优先使用明确的：

```python
np.stack(..., axis=...)
np.concatenate(..., axis=...)
```

轴含义更加清楚，代码更容易审查。

---

## 13. 不同尺寸图像不能直接 stack

```python
np.stack([image_720p, image_1080p])
```

会失败，因为 shape 不一致。需要先：

- resize 到统一尺寸；或
- padding 到统一尺寸；或
- 保持为列表逐张处理。

本阶段不依赖 OpenCV，可用模拟的统一尺寸数组练习。真正 resize 放到后续 OpenCV 阶段。

---

## 14. 纯 NumPy 模型输入预处理

```python
def prepare_model_input(image: np.ndarray) -> np.ndarray:
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"expected HWC color image, got {image.shape}")
    if image.dtype != np.uint8:
        raise ValueError(f"expected uint8, got {image.dtype}")

    normalized = image.astype(np.float32) / np.float32(255.0)
    chw = normalized.transpose(2, 0, 1)
    batch = chw[None, ...]
    return np.ascontiguousarray(batch)
```

契约：

```text
输入:  (H, W, 3), uint8, [0,255]
输出:  (1, 3, H, W), float32, [0,1], C 连续
修改输入: 否
```

---

## 15. 批量预处理

```python
def prepare_model_batch(frames: np.ndarray) -> np.ndarray:
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError(f"expected NHWC batch, got {frames.shape}")
    if frames.dtype != np.uint8:
        raise ValueError(f"expected uint8, got {frames.dtype}")

    normalized = frames.astype(np.float32) / np.float32(255.0)
    nchw = normalized.transpose(0, 3, 1, 2)
    return np.ascontiguousarray(nchw)
```

不要逐帧调用单图函数再 `stack`，规则批次可以一次性向量化转换。

---

## 16. BGR 与 RGB 不属于轴转换

OpenCV 常返回 BGR，模型通常要求 RGB。BGR→RGB 是通道内容重排：

```python
rgb = bgr[..., ::-1]
```

HWC→CHW 是轴顺序变化：

```python
chw = rgb.transpose(2, 0, 1)
```

它们是两个不同操作，必须分别说明。通道反转可能产生负 stride，因此最终使用 `ascontiguousarray`。

---

## 17. shape 断言与数据契约

每个关键转换后加入开发期断言：

```python
assert normalized.dtype == np.float32
assert normalized.shape == image.shape
assert chw.shape == (3, image.shape[0], image.shape[1])
assert batch.shape == (1, 3, image.shape[0], image.shape[1])
assert batch.flags.c_contiguous
```

公共输入错误使用明确异常，内部不变量可以使用断言。不要用断言校验不可信外部输入，因为优化模式可能移除断言。

---

## 18. 常见错误

### 错误 1：用 reshape 交换轴

结果 shape 可能正确，数据语义错误，是最危险的静默错误。

### 错误 2：忘记批次轴

模型需要 `(N, C, H, W)`，却传入 `(C, H, W)`。

### 错误 3：无参数 squeeze

尺寸恰好为 1 的有效轴被删除。

### 错误 4：先连续化再 transpose

转置后又变为非连续，应在最终布局后连续化。

### 错误 5：混淆 RGB/BGR 与 HWC/CHW

前者是通道顺序，后者是轴布局。

---

## 19. 今日综合任务：模型输入适配器

实现：

```python
def prepare_model_batch(
    frames: np.ndarray,
    *,
    input_color: str = "rgb",
    output_color: str = "rgb",
    normalize: bool = True,
) -> np.ndarray:
    ...
```

要求：

1. 输入必须是 `(N, H, W, 3)` `uint8`。
2. 支持 RGB/BGR 通道转换。
3. 可选择是否归一化。
4. 输出统一为 `(N, 3, H, W)` `float32`。
5. 输出 C 连续。
6. 输入不被修改。
7. 支持 N=0 的空批次，但 H、W、C 契约仍合法。
8. 错误颜色名称产生明确异常。

---

## 20. 建议测试

- HWC→CHW 后每个像素通道位置正确。
- NHWC→NCHW→NHWC 往返数据一致。
- RGB→BGR 通道值正确交换。
- 输出 dtype 为 `float32`。
- 输出范围在 `[0,1]`。
- 输出 C 连续。
- 输入数组不变。
- 灰度图、错误通道数、错误 dtype 被拒绝。
- `squeeze(axis=0)` 只删除批次轴。
- `stack` 与 `concatenate` 输出 shape 符合预期。

---

## 21. 今日验收清单

- [ ] 能解释 reshape 与 transpose 的区别。
- [ ] 不会使用 reshape 实现 HWC→CHW。
- [ ] 能完成 HWC↔CHW 和 NHWC↔NCHW。
- [ ] 会使用 `moveaxis`。
- [ ] 会安全使用 `expand_dims` 和 `squeeze(axis=...)`。
- [ ] 能解释 `ravel` 与 `flatten`。
- [ ] 能检查并修复连续性。
- [ ] 能正确选择 `stack` 或 `concatenate`。
- [ ] 能区分颜色通道顺序和轴布局。
- [ ] 完成模型输入适配器。

## 22. 今日最低交付物

```text
src/hoyo_vision/image_ops.py
tests/test_layout_ops.py
examples/day07_model_input_demo.py
```

建议提交信息：

```text
feat: add model input layout conversion
```

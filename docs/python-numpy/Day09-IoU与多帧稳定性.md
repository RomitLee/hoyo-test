# Day 9：IoU 与多帧稳定性

> 上一课：[Day 08：检测框坐标系统](./Day08-检测框坐标系统.md)
> 下一课：[Day 10：NumPy 视觉数据处理器综合项目](./Day10-NumPy视觉数据处理器综合项目.md)

## 1. 今日目标

单帧检测会抖动、偶发漏检和误检。今天学习用 IoU 描述位置重合，并用连续多帧证据判断目标是否稳定。建议用时：**2～3 小时**。

完成后应能：

- 手算并实现两组框的两两 IoU。
- 使用 broadcasting 输出 `(N, M)` IoU 矩阵。
- 正确处理不相交、零面积和空输入。
- 基于类别、IoU、置信度和连续帧数构建稳定性规则。
- 理解简单稳定性判断与完整目标跟踪的边界。

---

## 2. IoU 定义

```text
IoU = 交集面积 / 并集面积
并集面积 = area_a + area_b - intersection
```

取值范围通常为 `[0,1]`：

- 完全重合：1。
- 完全不相交：0。
- 部分重合：0～1。

IoU 只描述矩形重合程度，不表示类别是否相同，也不表示 OCR 文本是否相同。

---

## 3. 单对框计算

```python
def single_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    left = max(float(box_a[0]), float(box_b[0]))
    top = max(float(box_a[1]), float(box_b[1]))
    right = min(float(box_a[2]), float(box_b[2]))
    bottom = min(float(box_a[3]), float(box_b[3]))

    intersection_width = max(0.0, right - left)
    intersection_height = max(0.0, bottom - top)
    intersection = intersection_width * intersection_height

    area_a = max(0.0, float(box_a[2] - box_a[0])) * max(
        0.0, float(box_a[3] - box_a[1])
    )
    area_b = max(0.0, float(box_b[2] - box_b[0])) * max(
        0.0, float(box_b[3] - box_b[1])
    )

    union = area_a + area_b - intersection
    return intersection / union if union > 0.0 else 0.0
```

这个版本适合验证公式，但不适合 N×M 大量框的双层循环。

---

## 4. 两两 IoU 的 shape 推导

输入：

```text
boxes_a: (N, 4)
boxes_b: (M, 4)
```

增加维度：

```text
boxes_a[:, None, :] → (N, 1, 4)
boxes_b[None, :, :] → (1, M, 4)
```

广播后：

```text
交集左上/右下 → (N, M, 2)
交集面积       → (N, M)
area_a         → (N,)
area_b         → (M,)
union          → (N, M)
IoU            → (N, M)
```

矩阵第 `[i, j]` 项表示 `boxes_a[i]` 与 `boxes_b[j]` 的 IoU。

---

## 5. 向量化实现

```python
import numpy as np


def box_iou(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    a = np.asarray(boxes_a, dtype=np.float32)
    b = np.asarray(boxes_b, dtype=np.float32)

    if a.ndim != 2 or a.shape[1] != 4:
        raise ValueError(f"boxes_a must have shape (N, 4), got {a.shape}")
    if b.ndim != 2 or b.shape[1] != 4:
        raise ValueError(f"boxes_b must have shape (M, 4), got {b.shape}")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("box coordinates must be finite")

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
        where=union > 0.0,
    )
```

`np.divide(..., where=...)` 避免零面积框造成除零和 `NaN`。

---

## 6. 空输入行为

```python
boxes_a = np.empty((0, 4), dtype=np.float32)
boxes_b = np.empty((3, 4), dtype=np.float32)
result = box_iou(boxes_a, boxes_b)
```

预期 shape：

```text
(0, 3)
```

反过来是 `(3, 0)`，两边都空是 `(0, 0)`。没有检测结果是正常状态，不应抛错。

---

## 7. 手工测试案例

### 完全重合

```text
A = [0, 0, 10, 10]
B = [0, 0, 10, 10]
IoU = 1
```

### 完全不相交

```text
A = [0, 0, 10, 10]
B = [20, 20, 30, 30]
IoU = 0
```

### 部分相交

```text
A = [0, 0, 10, 10]，面积 100
B = [5, 5, 15, 15]，面积 100
交集面积 25
并集面积 175
IoU = 25/175 ≈ 0.142857
```

先用手算案例保证公式正确，再使用随机数据比较参考循环与向量化实现。

---

## 8. IoU 阈值没有万能值

阈值取决于目标大小、检测抖动和任务：

```text
0.3～0.5：容忍位置变化，适合粗匹配
0.5～0.7：常用中等严格匹配
0.7 以上：位置需高度稳定
```

小目标移动几个像素，IoU 就可能显著下降。不要照搬固定阈值，应在自己的验证集和实际帧上统计。

---

## 9. 从单帧检测到多帧证据

一个简单稳定规则：

```text
同类别
AND 与前一稳定框 IoU >= 0.7
AND 当前置信度 >= 0.6
AND 连续出现 >= 3 帧
→ stable = true
```

同时规定丢失容忍：

```text
连续丢失 <= 1 帧 → 保留候选轨迹
连续丢失 > 1 帧  → 重置
```

这样可以过滤一帧误检，但不会因偶发漏检立即丢失状态。

---

## 10. 简单追踪状态

```python
from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class TrackState:
    label: str
    last_box: np.ndarray
    consecutive_hits: int = 1
    missed_frames: int = 0
    confidence_sum: float = 0.0

    @property
    def average_confidence(self) -> float:
        return self.confidence_sum / self.consecutive_hits
```

这里使用可变状态是有意的：追踪器需要跨帧更新。与 Day 2 的不可变检测结果不同，运行时状态对象可以可变，但所有权必须集中在追踪器内部。

---

## 11. 单目标稳定性判断器

```python
@dataclass(slots=True, frozen=True)
class StabilityConfig:
    iou_threshold: float = 0.7
    confidence_threshold: float = 0.6
    required_hits: int = 3
    max_missed_frames: int = 1
```

流程：

```text
当前没有候选
→ 高置信度检测创建候选

当前有候选
→ 类别相同且 IoU 达标：hits + 1，更新框
→ 无匹配：missed + 1
→ missed 超限：重置

hits 达标且平均置信度达标
→ stable
```

先实现单类别、单目标版本，理解状态更新后再扩展多目标。

---

## 12. 多目标匹配的最简策略

对于上一帧 A 个轨迹和当前帧 B 个检测：

1. 计算 `(A, B)` IoU 矩阵。
2. 将类别不一致的位置设为不可匹配。
3. 选择最高 IoU 配对。
4. 一个轨迹和一个检测只能使用一次。
5. 未匹配轨迹增加 missed。
6. 未匹配检测创建新候选。

简单贪心匹配可以用于学习，但不保证全局最优。更复杂场景会使用匈牙利算法、卡尔曼滤波、ByteTrack 等；本阶段不必实现。

---

## 13. 类别掩码

```python
# track_class_ids: (A,)
# detection_class_ids: (B,)
same_class = track_class_ids[:, None] == detection_class_ids[None, :]

ious = box_iou(track_boxes, detection_boxes)
valid_ious = np.where(same_class, ious, -1.0)
```

这样不同类别不会被误匹配。还可以增加置信度和区域限制。

---

## 14. 稳定不等于真实

连续多帧稳定出现只能降低偶发抖动，不能证明检测一定正确。模型可能持续把同一 UI 识别成错误类别。

生产系统还应结合：

- 类别置信度。
- OCR 内容与规则。
- 场景状态。
- 目标区域约束。
- 多模型或模板匹配证据。
- 未知状态停止策略。

---

## 15. 防止状态振荡

进入和退出可以使用不同阈值：

```text
进入稳定：连续 3 帧，IoU >= 0.7，score >= 0.7
保持稳定：允许 1 帧缺失，score >= 0.5
退出稳定：连续 2 帧无匹配
```

这叫滞回思路，可以避免状态在阈值附近频繁切换。

---

## 16. 帧率与时间

“连续 3 帧”在 30 FPS 下约 100ms，在每秒抽样 2 帧时约 1.5 秒。配置最好同时理解为时间窗口：

```text
required_duration_ms
max_missing_duration_ms
```

学习阶段可以先按帧数实现，但日志中记录时间戳，为后续基于时间的稳定性判断留接口。

---

## 17. 可观测性

稳定性日志应包括：

```text
frame_id
label
best_iou
current_confidence
consecutive_hits
missed_frames
stable
state_transition
```

只在状态变化时记录 `INFO`，逐帧匹配细节使用 `DEBUG`。

---

## 18. 今日综合任务

### 任务 A：IoU 模块

实现：

```python
box_iou(boxes_a, boxes_b) -> (N, M)
best_iou_per_box(boxes_a, boxes_b) -> (N,)
```

空 `boxes_b` 时，`best_iou_per_box` 返回长度 N 的全零数组，不能直接对空轴调用 `max`。

### 任务 B：稳定性判断器

输入每帧检测结果：

```python
boxes: np.ndarray       # (M, 4)
scores: np.ndarray      # (M,)
class_ids: np.ndarray   # (M,)
```

输出：

```python
@dataclass(slots=True, frozen=True)
class StableObject:
    class_id: int
    confidence: float
    bbox: tuple[float, float, float, float]
    consecutive_hits: int
    stable: bool
```

要求：

- 同类别才匹配。
- 使用 IoU 阈值。
- 使用置信度阈值。
- 支持一帧漏检容忍。
- 未知或歧义匹配不输出稳定动作依据。
- 可重置状态。

---

## 19. 建议测试

- 完全重合 IoU=1。
- 完全不相交 IoU=0。
- 部分相交与手算一致。
- 零面积框不产生 `NaN`。
- `(0,4)` 对 `(M,4)` 输出 `(0,M)`。
- 向量化结果与循环参考一致。
- 同一目标连续 3 帧后稳定。
- 第二帧位置轻微移动仍匹配。
- 不同类别不匹配。
- 一帧误报不会稳定。
- 允许一次漏检后恢复。
- 连续漏检超限后重置。
- 状态变化日志正确。

---

## 20. 性能与规模

IoU 矩阵占用约为 `N × M × 4` 字节（float32）。普通 UI 检测框数量少，问题不大；若 N、M 很大，矩阵和中间 `(N,M,2)` 数组会消耗内存。

优化前先测量。可以按类别分组或空间区域过滤，减少无意义配对，而不是立即写复杂底层代码。

---

## 21. 今日验收清单

- [ ] 能手算 IoU。
- [ ] 能解释 `(N,M)` 输出矩阵每个轴。
- [ ] IoU 实现没有 N×M 双层 Python 循环。
- [ ] 零面积框不会产生 `NaN`。
- [ ] 空输入返回正确 shape。
- [ ] 知道 IoU 阈值取决于目标和场景。
- [ ] 能用类别掩码限制匹配。
- [ ] 能实现连续命中与漏检容忍。
- [ ] 理解稳定性判断不等于完整目标跟踪。
- [ ] 完成 IoU 和稳定目标判断器。

## 22. 今日最低交付物

```text
src/hoyo_vision/box_ops.py
src/hoyo_vision/stability.py
tests/test_iou.py
tests/test_stability.py
examples/day09_stability_demo.py
```

建议提交信息：

```text
feat: add IoU and multi-frame stability detection
```

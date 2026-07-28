# Day 1：Python 对象模型、容器和常用表达式

> 所属阶段：[Python 与 NumPy 10 天速学方案](../../Python与NumPy十天速学方案.md)
>
> 适用对象：具有其他语言编程经验，不需要重新学习变量、循环和条件语句，希望快速建立 Python 工程直觉的人。
>
> 建议用时：**2～3 小时**。

---

## 1. 今天要解决什么问题

今天不背 Python 语法，重点解决从 Java、C#、C++ 或 JavaScript 转到 Python 时最容易产生的认知偏差：

1. Python 变量究竟保存什么？
2. 为什么修改一个列表，另一个变量也会变化？
3. 函数参数到底是值传递还是引用传递？
4. 浅拷贝和深拷贝为什么经常达不到预期？
5. `==` 和 `is` 有什么本质区别？
6. 在视觉识别项目中，应怎样选择 `list`、`tuple`、`dict` 和 `set`？
7. 如何用推导式、`enumerate`、`zip`、`any`、`all` 和 `sorted` 写出清晰的数据处理代码？
8. 如何在不修改原始识别数据的情况下完成过滤、排序和转换？

完成今天的学习后，你需要能独立编写一个“小型检测结果清洗器”。

---

## 2. 今日时间安排

| 环节 | 建议时间 | 内容 |
|---|---:|---|
| 建立对象模型 | 30 分钟 | 名称、对象、身份、类型、值 |
| 可变性与拷贝 | 35 分钟 | 别名、浅拷贝、深拷贝、参数传递 |
| 容器选型 | 25 分钟 | `list`、`tuple`、`dict`、`set` |
| 常用表达式 | 35 分钟 | 推导式和常用内置函数 |
| 综合练习 | 40～60 分钟 | 检测结果清洗器 |
| 复盘 | 10 分钟 | 验收清单与 Git 提交 |

建议所有示例都遵循以下流程：

```text
先预测结果 → 再运行代码 → 解释原因 → 修改输入再次验证
```

---

# 第一部分：Python 对象模型

## 3. 变量是名称，不是固定类型的盒子

理解 Python 最重要的一句话：

> Python 变量是绑定到对象的名称；类型属于对象，而不是变量名称。

```python
value = 100
value = "task_panel"
```

这里不是把一个“整型变量”变成“字符串变量”，而是名称 `value` 先绑定到整数对象，随后重新绑定到字符串对象。

可以从三个维度观察对象：

```python
value = [1, 2, 3]

print(id(value))     # 对象身份，在对象生命周期内保持唯一
print(type(value))   # 对象类型
print(value)         # 对象值
```

### 3.1 赋值不会自动复制对象

```python
source = ["task_panel", "npc"]
alias = source

alias.append("button")

print(source)
print(alias)
print(source is alias)
```

输出中的两个列表都会包含 `button`，并且 `source is alias` 为 `True`。

内存关系可以理解为：

```text
source ─┐
        ├──> 同一个 list 对象
alias  ─┘
```

这不是 Python 的异常行为，而是赋值语句本来的含义：**建立名称到对象的绑定**。

### 3.2 重新绑定和原地修改不是一回事

重新绑定：

```python
items = [1, 2, 3]
other = items

items = [4, 5, 6]

print(other)  # [1, 2, 3]
```

原地修改：

```python
items = [1, 2, 3]
other = items

items.append(4)

print(other)  # [1, 2, 3, 4]
```

区别：

- `items = ...`：让名称 `items` 改为绑定另一个对象。
- `items.append(...)`：修改 `items` 当前指向的列表对象。

### 3.3 增强赋值需要特别小心

对不可变对象：

```python
count = 10
original = count
count += 1

print(original)  # 10
```

对列表：

```python
items = [1, 2]
alias = items
items += [3]

print(alias)  # [1, 2, 3]
```

列表的 `+=` 通常会原地扩展列表，因此别名也能看到变化。不要简单地把 `x += y` 永远理解为 `x = x + y`；具体行为与对象类型实现有关。

---

## 4. 可变对象与不可变对象

### 4.1 常见分类

| 类型 | 是否可变 | 说明 |
|---|---|---|
| `int`、`float`、`bool` | 否 | 运算通常产生新对象 |
| `str` | 否 | 字符串内容不能原地修改 |
| `tuple` | 否 | 元组自身结构不能修改 |
| `bytes` | 否 | 不可变字节序列 |
| `list` | 是 | 可追加、删除、替换元素 |
| `dict` | 是 | 可增加、删除、修改键值对 |
| `set` | 是 | 可增加和删除元素 |
| `bytearray` | 是 | 可变字节序列 |
| `numpy.ndarray` | 是 | 后续阶段的核心可变对象 |

### 4.2 “元组不可变”不代表内部对象不可变

```python
record = ("task_panel", [100, 80, 500, 420])
record[1][0] = 120

print(record)
```

元组没有更换第二个元素；它仍然引用原来的列表。发生变化的是元组内部列表的内容。

因此：

> 容器不可变，只意味着容器自身保存的引用不能替换，不保证它引用的所有对象都不可变。

### 4.3 可变性为何对视觉项目重要

视觉流水线会经过多个阶段：

```text
原始检测结果
→ 坐标裁剪
→ 置信度过滤
→ OCR 补充
→ 状态融合
→ JSON 输出
```

如果多个阶段共享同一个可变字典或列表，并且每一步都原地修改，就容易出现：

- 原始模型输出被污染。
- 调试时无法还原问题发生前的数据。
- 测试之间相互影响。
- 多线程或异步处理时产生状态竞争。
- 日志记录的“原始数据”其实已经被后续步骤修改。

第一阶段建议采用简单原则：

1. 输入默认只读。
2. 转换函数默认返回新对象。
3. 需要原地修改时，在函数名、参数或文档中明确说明。
4. 跨模块传递的领域数据，后续优先使用不可变 dataclass。

---

## 5. `==` 与 `is`

### 5.1 `==` 比较值

```python
left = [1, 2, 3]
right = [1, 2, 3]

print(left == right)  # True
```

### 5.2 `is` 比较对象身份

```python
print(left is right)  # False
```

虽然两个列表的内容相同，但它们是两个不同对象。

### 5.3 `is` 的正确典型用法

判断 `None`：

```python
if result is None:
    ...
```

不要写：

```python
if result == None:
    ...
```

### 5.4 不要依赖整数或字符串驻留现象

某些小整数和短字符串可能被解释器复用：

```python
first = 100
second = 100
print(first is second)
```

这个结果不能作为业务逻辑依据。比较数值、字符串、列表和数据模型内容时使用 `==`；只有明确需要判断“是否为同一个对象”时才使用 `is`。

### 5.5 实战规则

```text
判断值相等       → ==
判断值不相等     → !=
判断是否为 None  → is None / is not None
判断是否同一对象 → is / is not
```

---

## 6. 函数参数：对象共享，而不是简单的“值/引用二选一”

Python 经常被描述为“引用传递”或“值传递”，这两种简化描述都容易误导。更准确的理解是：

> 调用函数时，实参对象会绑定到函数内部的形参名称；函数内外可能共享同一个对象。

这种语义也常被称为 **call by sharing**。

### 6.1 修改可变对象，调用方可以观察到

```python
def append_label(labels: list[str]) -> None:
    labels.append("npc")


source = ["task_panel"]
append_label(source)
print(source)  # ['task_panel', 'npc']
```

### 6.2 重新绑定形参，不会改变调用方名称

```python
def replace_labels(labels: list[str]) -> None:
    labels = ["button"]


source = ["task_panel"]
replace_labels(source)
print(source)  # ['task_panel']
```

函数中的 `labels` 只是改为绑定另一个列表，外部的 `source` 仍然绑定原列表。

### 6.3 推荐写成无副作用转换函数

有副作用：

```python
def remove_low_scores(results: list[dict], threshold: float) -> None:
    results[:] = [item for item in results if item["score"] >= threshold]
```

默认更推荐：

```python
def filter_by_score(
    results: list[dict[str, object]],
    threshold: float,
) -> list[dict[str, object]]:
    return [item for item in results if float(item["score"]) >= threshold]
```

后者更容易：

- 单元测试。
- 组合处理步骤。
- 保留原始数据。
- 定位错误。
- 在未来并行化。

---

## 7. 可变默认参数陷阱

### 7.1 错误示例

```python
def collect_label(label: str, labels: list[str] = []) -> list[str]:
    labels.append(label)
    return labels


print(collect_label("task_panel"))
print(collect_label("npc"))
```

第二次调用会继续使用第一次调用的列表，因为默认参数在**函数定义时**求值，而不是每次调用时重新创建。

### 7.2 正确写法

```python
def collect_label(
    label: str,
    labels: list[str] | None = None,
) -> list[str]:
    if labels is None:
        labels = []

    labels.append(label)
    return labels
```

### 7.3 不可变默认参数通常没有这个问题

```python
def filter_by_score(results, threshold: float = 0.5):
    ...
```

`float`、`int`、`str`、`None` 等不可变对象通常适合作为默认值。

### 7.4 工程规则

函数默认参数中看到下面这些内容，要立刻提高警惕：

```python
[]
{}
set()
某个可变类的实例
```

通常应改为 `None`，在函数内部创建。

---

# 第二部分：复制与数据隔离

## 8. 赋值、浅拷贝与深拷贝

使用嵌套识别数据进行实验：

```python
import copy

source = [
    {
        "label": "task_panel",
        "score": 0.93,
        "box": [100, 80, 500, 420],
    }
]

assigned = source
shallow = source.copy()
deep = copy.deepcopy(source)

source[0]["box"][0] = 120

print(assigned)
print(shallow)
print(deep)
```

### 8.1 三种方式的区别

| 操作 | 最外层容器 | 内层字典 | 内层 `box` 列表 |
|---|---|---|---|
| `assigned = source` | 共享 | 共享 | 共享 |
| `source.copy()` | 新建 | 共享 | 共享 |
| `copy.deepcopy(source)` | 新建 | 新建 | 新建 |

关系示意：

```text
浅拷贝：
source  ──> 外层列表 A ──┐
                         ├──> 同一个内部字典和 box
shallow ──> 外层列表 B ──┘

深拷贝：
deep ──> 新外层列表 ──> 新字典 ──> 新 box
```

### 8.2 常见浅拷贝写法

```python
new_list = old_list.copy()
new_list = list(old_list)
new_list = old_list[:]

new_dict = old_dict.copy()
new_dict = dict(old_dict)
```

它们只复制最外层容器。

### 8.3 不要无脑使用深拷贝

`deepcopy` 并不是默认最佳方案：

- 复制大型图像数组会消耗大量内存和时间。
- 有些对象不能或不适合深拷贝。
- 深拷贝可能掩盖数据模型设计问题。
- 流水线每一步都深拷贝，会造成严重性能浪费。

视觉项目更推荐：

1. 原始帧和大数组明确所有权。
2. 小型元数据使用不可变结构。
3. 只复制即将修改的那一层。
4. NumPy 阶段明确区分视图和副本。

### 8.4 定向复制比全量深拷贝更清晰

如果只想调整一个检测框：

```python
def update_box_x1(
    detection: dict[str, object],
    new_x1: float,
) -> dict[str, object]:
    old_box = detection["box"]
    if not isinstance(old_box, list):
        raise TypeError("box must be a list")

    new_box = old_box.copy()
    new_box[0] = new_x1

    return {
        **detection,
        "box": new_box,
    }
```

这里只复制将要修改的字典和 `box`，没有复制所有无关数据。

---

## 9. 如何验证对象是否共享

### 9.1 使用 `is`

```python
print(source is assigned)
print(source[0] is shallow[0])
```

### 9.2 使用 `id`

```python
print(id(source))
print(id(assigned))
print(id(shallow))
```

`id` 适合学习和调试，不要把具体数值写入业务规则。

### 9.3 使用“修改实验”

最可靠的理解方式：

1. 在纸上画出名称与对象的关系。
2. 预测修改某层后哪些变量会变化。
3. 运行代码验证。
4. 对最外层和内层分别测试。

---

# 第三部分：容器选型

## 10. `list`：有顺序、可变、允许重复

适合：

- 一帧中的检测结果序列。
- 视频帧按时间排列的结果。
- 需要追加、删除或排序的数据。

```python
detections = [
    {"label": "task_panel", "score": 0.93},
    {"label": "npc", "score": 0.87},
]
```

常用操作复杂度的直觉：

| 操作 | 一般复杂度 |
|---|---:|
| 按索引读取 | `O(1)` |
| 尾部追加 | 均摊 `O(1)` |
| 中间插入/删除 | `O(n)` |
| 按值查找 | `O(n)` |
| 排序 | `O(n log n)` |

不要频繁使用列表头部的 `insert(0, ...)` 或 `pop(0)` 实现队列；队列应考虑 `collections.deque`。

---

## 11. `tuple`：有顺序、不可变、允许重复

适合：

- 固定结构的数据。
- 图像尺寸 `(height, width)`。
- RGB 值 `(red, green, blue)`。
- 不希望容器结构被修改的返回值。

```python
image_size = (720, 1280)
color = (0, 255, 0)
```

对于检测框，初期可以用元组：

```python
box = (100.0, 80.0, 500.0, 420.0)
```

但当字段语义越来越多时，后续更建议使用 dataclass，而不是依赖 `box[0]`、`box[1]` 这种位置记忆。

---

## 12. `dict`：键到值的映射

适合：

- 模型原始输出。
- JSON 数据。
- 类别编号到名称的映射。
- 配置项。
- 按唯一 ID 查找对象。

```python
class_names = {
    0: "task_panel",
    1: "npc",
    2: "button",
}
```

### 12.1 安全读取

确定键必须存在时：

```python
score = detection["score"]
```

键可选时：

```python
text = detection.get("ocr_text")
```

提供默认值：

```python
text = detection.get("ocr_text", "")
```

不要用 `.get()` 隐藏本应立即暴露的数据错误。如果 `score` 是协议中的必填字段，缺失时就应该抛出 `KeyError` 或经过显式校验报错。

### 12.2 合并字典

```python
normalized = {
    **raw_detection,
    "score": float(raw_detection["score"]),
    "stable": False,
}
```

或使用字典合并运算符：

```python
normalized = raw_detection | {"stable": False}
```

两种方式都会创建新字典；如果键重复，右侧值覆盖左侧值。

---

## 13. `set`：无重复元素的集合

适合：

- 检查某帧出现了哪些类别。
- 类别白名单。
- 去重。
- 集合间的交集、并集和差集。

```python
visible_labels = {"task_panel", "npc", "button"}
allowed_labels = {"task_panel", "npc"}

unknown_labels = visible_labels - allowed_labels
common_labels = visible_labels & allowed_labels
all_labels = visible_labels | allowed_labels
```

不要依赖集合顺序。如果需要稳定顺序，应在输出时显式排序：

```python
ordered_labels = sorted(visible_labels)
```

### 13.1 去重但保留原顺序

不要直接使用 `set`，可以利用字典保持插入顺序：

```python
labels = ["npc", "button", "npc", "task_panel"]
unique_labels = list(dict.fromkeys(labels))

print(unique_labels)
# ['npc', 'button', 'task_panel']
```

---

## 14. 容器选择速查表

| 需求 | 推荐容器 |
|---|---|
| 按时间保存一系列检测结果 | `list` |
| 保存固定的图像尺寸 | `tuple[int, int]` |
| 用类别 ID 查类别名称 | `dict[int, str]` |
| 判断类别是否在白名单中 | `set[str]` / `frozenset[str]` |
| 先进先出地缓存最近帧 | `collections.deque` |
| 统计每个类别出现次数 | `collections.Counter` |
| 保存稳定、可读的领域对象 | 下一天学习 `dataclass` |

性能不是唯一标准。容器类型应优先表达数据语义，使读代码的人一眼知道它代表序列、映射、固定结构还是集合。

---

# 第四部分：常用表达式

## 15. 列表推导式

原始写法：

```python
kept = []
for detection in raw_detections:
    if detection["score"] >= 0.5:
        kept.append(detection)
```

推导式：

```python
kept = [
    detection
    for detection in raw_detections
    if detection["score"] >= 0.5
]
```

### 15.1 推荐原则

适合推导式：

- 单层映射。
- 单层过滤。
- 逻辑可以快速读懂。

不适合推导式：

- 多层嵌套。
- 包含异常处理。
- 有多个副作用。
- 业务分支很多。
- 一行超过正常可读范围。

复杂逻辑宁可使用普通循环或拆成命名函数。

---

## 16. 字典推导式和集合推导式

### 16.1 字典推导式

```python
class_names = ["task_panel", "npc", "button"]
class_map = {index: name for index, name in enumerate(class_names)}
```

按标签构建最高置信度：

```python
best_scores = {
    label: max(
        item["score"]
        for item in raw_detections
        if item["label"] == label
    )
    for label in {item["label"] for item in raw_detections}
}
```

这个例子虽然可行，但已经较复杂。生产代码可以拆成循环，以获得更好的性能和可读性。

### 16.2 集合推导式

```python
visible_labels = {
    item["label"]
    for item in raw_detections
    if item["score"] >= 0.5
}
```

---

## 17. `enumerate`：同时获取序号和值

不推荐：

```python
for index in range(len(detections)):
    detection = detections[index]
    print(index, detection)
```

推荐：

```python
for index, detection in enumerate(detections):
    print(index, detection)
```

从 1 开始编号：

```python
for number, detection in enumerate(detections, start=1):
    print(number, detection["label"])
```

在视觉项目中，`enumerate` 可用于生成帧编号和检测序号。

---

## 18. `zip`：并行遍历多个序列

```python
labels = ["task_panel", "npc", "button"]
scores = [0.93, 0.87, 0.31]
boxes = [
    [100, 80, 500, 420],
    [600, 200, 750, 600],
    [20, 20, 80, 60],
]

for label, score, box in zip(labels, scores, boxes, strict=True):
    print(label, score, box)
```

### 18.1 为什么建议 `strict=True`

默认情况下，`zip` 遇到最短序列结束就停止，可能静默丢失数据：

```python
labels = ["npc", "button"]
scores = [0.9]
```

使用 `strict=True`，长度不一致时会抛出 `ValueError`，更适合数据流水线。

### 18.2 什么时候不用 `zip`

如果多个序列在业务上必须永远绑定在一起，长期维护时应考虑把它们组合成字典、dataclass 或 NumPy 结构，而不是一直依赖多个平行列表。

---

## 19. `any` 与 `all`

### 19.1 `any`：至少一个条件为真

```python
has_task_panel = any(
    item["label"] == "task_panel" and item["score"] >= 0.5
    for item in raw_detections
)
```

### 19.2 `all`：所有条件都为真

```python
all_boxes_valid = all(
    len(item["box"]) == 4
    for item in raw_detections
)
```

### 19.3 空序列行为

```python
any([])  # False
all([])  # True
```

`all([]) == True` 源于逻辑上的“空真”。在业务代码中要确认这是否符合需求。

例如“所有检测框都合法”在没有检测框时可能返回 `True`，但如果业务要求至少存在一个检测框，则应写：

```python
has_valid_detections = bool(detections) and all(
    is_valid_detection(item)
    for item in detections
)
```

---

## 20. `sorted` 与原地 `sort`

### 20.1 `sorted` 返回新列表

```python
ordered = sorted(
    raw_detections,
    key=lambda item: item["score"],
    reverse=True,
)
```

原始列表顺序不变。

### 20.2 `list.sort()` 原地修改

```python
raw_detections.sort(
    key=lambda item: item["score"],
    reverse=True,
)
```

函数返回 `None`，并改变原列表。

### 20.3 初期推荐

数据处理流水线默认使用 `sorted`，因为保留原始输入更容易调试。如果明确拥有这个列表，且关心内存开销，才考虑原地 `sort()`。

### 20.4 多字段排序

置信度降序，同分时标签升序：

```python
ordered = sorted(
    raw_detections,
    key=lambda item: (-item["score"], item["label"]),
)
```

---

## 21. `min`、`max`、`sum` 和生成器表达式

找置信度最高的结果：

```python
best = max(raw_detections, key=lambda item: item["score"])
```

处理空列表：

```python
best = max(
    raw_detections,
    key=lambda item: item["score"],
    default=None,
)
```

计算平均置信度：

```python
average_score = (
    sum(item["score"] for item in raw_detections) / len(raw_detections)
    if raw_detections
    else 0.0
)
```

这里只需要逐个消费值，不需要构建一个新列表，因此使用生成器表达式：

```python
sum(item["score"] for item in raw_detections)
```

而不是：

```python
sum([item["score"] for item in raw_detections])
```

---

## 22. 解包表达式

### 22.1 序列解包

```python
x1, y1, x2, y2 = detection["box"]
```

如果元素数量不是 4，会立即报错，这反而有助于发现数据问题。

### 22.2 星号收集

```python
first, *middle, last = [1, 2, 3, 4, 5]
```

### 22.3 合并序列和映射

```python
combined_labels = [*primary_labels, *extra_labels]

result = {
    **raw_detection,
    "stable": False,
}
```

### 22.4 函数调用解包

```python
def box_area(x1: float, y1: float, x2: float, y2: float) -> float:
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


area = box_area(*detection["box"])
```

只有在数据结构和参数顺序清晰、稳定时才这样使用。

---

## 23. 真值判断与短路求值

常见假值：

```text
False
None
0
0.0
""
[]
{}
set()
```

判断列表非空：

```python
if detections:
    ...
```

但不要把“缺失”和“数值为 0”混为一谈：

```python
score = detection.get("score")

if score is None:
    raise ValueError("score is required")
```

如果写成 `if not score`，那么合法的 `0.0` 也会被当成缺失。

### 23.1 `and` 和 `or` 返回操作数

```python
name = configured_name or "unknown"
```

这不是只返回 `True` 或 `False`，而是返回其中一个操作数。

这种写法适合简单默认值，但如果 `0`、空字符串或空列表是合法值，应显式判断 `None`。

---

# 第五部分：面向视觉项目的综合练习

## 24. 练习输入

```python
raw_detections = [
    {
        "label": "task_panel",
        "score": 0.93,
        "box": [100, 80, 500, 420],
    },
    {
        "label": "button",
        "score": 0.31,
        "box": [20, 20, 80, 60],
    },
    {
        "label": "npc",
        "score": 0.87,
        "box": [600, 200, 750, 600],
    },
    {
        "label": "npc",
        "score": 0.76,
        "box": [610, 210, 752, 598],
    },
]
```

---

## 25. 基础练习

### 练习 1：置信度过滤

保留 `score >= 0.5` 的结果，并确保不修改 `raw_detections`。

期望保留：

```text
task_panel, npc, npc
```

### 练习 2：排序

将过滤后的结果按置信度从高到低排序，不修改输入列表。

### 练习 3：提取标签

分别得到：

1. 保留重复项的标签列表。
2. 去重但不保证顺序的标签集合。
3. 去重并保留首次出现顺序的标签列表。

### 练习 4：存在性判断

判断是否存在置信度不低于 `0.8` 的 `task_panel`。

### 练习 5：完整性判断

判断每个结果是否都满足：

- 存在 `label`。
- 存在 `score`。
- 存在 `box`。
- `box` 恰好包含 4 个值。

### 练习 6：最高置信度

找出置信度最高的结果；空输入时返回 `None`。

### 练习 7：类别统计

统计每个标签出现的次数，期望得到：

```python
{
    "task_panel": 1,
    "button": 1,
    "npc": 2,
}
```

先用普通字典实现，再了解 `collections.Counter`。

---

## 26. 进阶练习：检测结果清洗器

实现下面的函数：

```python
def clean_detections(
    detections: list[dict[str, object]],
    *,
    confidence_threshold: float = 0.5,
    allowed_labels: set[str] | None = None,
) -> list[dict[str, object]]:
    """校验、过滤、转换并排序检测结果，不修改输入数据。"""
    ...
```

### 26.1 功能要求

1. `confidence_threshold` 必须在 `[0.0, 1.0]` 范围内。
2. 每个输入必须包含 `label`、`score` 和 `box`。
3. `label` 必须是非空字符串。
4. `score` 必须可转换为 `float`。
5. `box` 必须是长度为 4 的列表或元组。
6. 如果提供 `allowed_labels`，只保留白名单类别。
7. 过滤置信度低于阈值的结果。
8. 将 `score` 转为 `float`。
9. 将 `box` 转为四元素元组。
10. 为每个结果增加 `area` 字段。
11. 删除面积小于等于 0 的框。
12. 按置信度从高到低排序。
13. 不得修改输入列表、输入字典和内部 `box`。

### 26.2 推荐输出

```python
[
    {
        "label": "task_panel",
        "score": 0.93,
        "box": (100.0, 80.0, 500.0, 420.0),
        "area": 136000.0,
    },
    {
        "label": "npc",
        "score": 0.87,
        "box": (600.0, 200.0, 750.0, 600.0),
        "area": 60000.0,
    },
    {
        "label": "npc",
        "score": 0.76,
        "box": (610.0, 210.0, 752.0, 598.0),
        "area": 55096.0,
    },
]
```

---

## 27. 参考实现

建议先自己实现，再展开对照。

<details>
<summary>点击查看参考实现</summary>

```python
def clean_detections(
    detections: list[dict[str, object]],
    *,
    confidence_threshold: float = 0.5,
    allowed_labels: set[str] | None = None,
) -> list[dict[str, object]]:
    """校验、过滤、转换并排序检测结果，不修改输入数据。"""
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0.0 and 1.0")

    cleaned: list[dict[str, object]] = []

    for index, detection in enumerate(detections):
        missing_keys = {"label", "score", "box"} - detection.keys()
        if missing_keys:
            raise ValueError(
                f"detection at index {index} is missing keys: "
                f"{sorted(missing_keys)}"
            )

        label = detection["label"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                f"detection at index {index} has invalid label: {label!r}"
            )

        if allowed_labels is not None and label not in allowed_labels:
            continue

        try:
            score = float(detection["score"])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"detection at index {index} has invalid score: "
                f"{detection['score']!r}"
            ) from error

        if score < confidence_threshold:
            continue

        raw_box = detection["box"]
        if (
            not isinstance(raw_box, (list, tuple))
            or len(raw_box) != 4
        ):
            raise ValueError(
                f"detection at index {index} must have a four-value box"
            )

        try:
            x1, y1, x2, y2 = (float(value) for value in raw_box)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"detection at index {index} contains non-numeric box values"
            ) from error

        width = max(0.0, x2 - x1)
        height = max(0.0, y2 - y1)
        area = width * height

        if area <= 0.0:
            continue

        cleaned.append(
            {
                "label": label,
                "score": score,
                "box": (x1, y1, x2, y2),
                "area": area,
            }
        )

    return sorted(
        cleaned,
        key=lambda item: float(item["score"]),
        reverse=True,
    )
```

</details>

### 27.1 为什么这个实现没有直接复制原字典

参考实现只选择输出协议需要的字段，并为每个结果创建一个新字典。这样可以：

- 防止不受控字段进入下游。
- 明确输出数据契约。
- 避免共享输入字典和 `box` 列表。
- 方便未来替换为 dataclass。

### 27.2 可以进一步改进的地方

Day 2 学完 dataclass 和类型后，可以改进：

- 用 `BoundingBox` 代替四元素元组。
- 用 `Detection` 代替普通字典。
- 用自定义异常区分输入校验错误。
- 用静态类型明确每个字段。
- 使用 pytest 参数化测试。

---

## 28. 建议测试场景

即使今天还未系统学习 pytest，也应手动验证以下情况：

| 场景 | 期望行为 |
|---|---|
| 正常数据 | 返回过滤、转换、排序后的新列表 |
| 空列表 | 返回空列表 |
| 阈值为 0 | 保留所有合法正面积框 |
| 阈值为 1 | 只保留分数为 1 的框 |
| 阈值小于 0 或大于 1 | 抛出 `ValueError` |
| 缺少必填字段 | 抛出带索引信息的异常 |
| `score` 为字符串数字 | 转换成 `float` |
| `score` 为非法字符串 | 抛出 `ValueError` |
| `box` 长度不是 4 | 抛出 `ValueError` |
| 坐标不可转换成数字 | 抛出 `ValueError` |
| `x2 <= x1` 或 `y2 <= y1` | 过滤零面积框 |
| 标签不在白名单 | 过滤 |
| 输入有嵌套列表 | 调用后输入保持不变 |

验证未修改原数据：

```python
import copy

original = copy.deepcopy(raw_detections)
result = clean_detections(raw_detections)

assert raw_detections == original
assert result is not raw_detections
```

---

# 第六部分：常见错误

## 29. 错误一：一边遍历一边删除列表元素

不推荐：

```python
for item in detections:
    if item["score"] < 0.5:
        detections.remove(item)
```

列表长度和索引在遍历过程中改变，可能跳过元素。

推荐创建新列表：

```python
kept = [item for item in detections if item["score"] >= 0.5]
```

---

## 30. 错误二：为了复制嵌套数据只调用 `.copy()`

```python
copied = detection.copy()
copied["box"][0] = 120
```

`copied` 与原字典仍然共享内部 `box`。如果需要修改 `box`，应额外复制：

```python
copied = {
    **detection,
    "box": detection["box"].copy(),
}
```

---

## 31. 错误三：用 `is` 比较字符串或数字

错误：

```python
if detection["label"] is "npc":
    ...
```

正确：

```python
if detection["label"] == "npc":
    ...
```

---

## 32. 错误四：使用 `.get()` 吞掉必填字段错误

```python
score = detection.get("score", 0.0)
```

如果 `score` 是必填字段，这会把“数据协议错误”伪装成“低置信度结果”。应先校验键是否存在，再转换数据。

---

## 33. 错误五：推导式过度复杂

不建议把校验、转换、过滤、计算面积和排序全部塞进一个表达式。清晰的多行循环通常比炫技式的一行代码更适合工程项目。

---

## 34. 错误六：把空值判断写成统一的 `if not value`

如果 `0`、`0.0`、空字符串或空列表有明确业务意义，就不能与 `None` 混为一谈。

```python
if score is None:
    ...
```

比下面的写法更准确：

```python
if not score:
    ...
```

---

# 第七部分：今日复盘与验收

## 35. 必须能够口头解释的问题

1. `a = b` 是否复制了 `b`？
2. 重新绑定名称和原地修改对象有什么区别？
3. 为什么列表作为函数参数时可能被函数修改？
4. 为什么可变默认参数会跨调用保存状态？
5. 浅拷贝复制了哪一层？
6. `==` 和 `is` 分别比较什么？
7. 为什么 `all([])` 是 `True`？
8. `sorted()` 和 `.sort()` 有什么不同？
9. `zip(..., strict=True)` 能预防什么问题？
10. 怎样去重同时保留原顺序？

---

## 36. 今日验收清单

### 对象模型

- [ ] 理解变量是绑定到对象的名称。
- [ ] 能区分重新绑定和原地修改。
- [ ] 理解可变对象与不可变对象。
- [ ] 能解释函数参数的对象共享语义。
- [ ] 不会再写可变默认参数。
- [ ] 正确使用 `==` 和 `is`。

### 拷贝与数据隔离

- [ ] 能画出赋值、浅拷贝和深拷贝的引用关系。
- [ ] 知道浅拷贝仍会共享内部对象。
- [ ] 不会对大型视觉数据无脑 `deepcopy`。
- [ ] 转换函数默认不修改输入。

### 容器与表达式

- [ ] 能根据语义选择 `list`、`tuple`、`dict`、`set`。
- [ ] 会使用列表、字典和集合推导式。
- [ ] 会使用 `enumerate` 和 `zip(strict=True)`。
- [ ] 会使用 `any`、`all`、`sorted`、`min` 和 `max`。
- [ ] 会用生成器表达式避免无意义的临时列表。

### 实战

- [ ] 独立完成检测结果过滤和排序。
- [ ] 能提取、去重和统计类别。
- [ ] 能校验检测结果字段。
- [ ] 完成 `clean_detections`。
- [ ] 验证函数没有修改输入数据。
- [ ] 为至少 8 个边界场景做手动测试。

---

## 37. 今日最低交付物

建议在练习项目中形成：

```text
src/
└── hoyo_vision/
    └── detection_cleaner.py

examples/
└── day01_detection_cleaner_demo.py
```

最低要求：

1. `detection_cleaner.py` 包含 `clean_detections`。
2. 示例脚本可以处理本文给出的模拟数据。
3. 输出过滤和排序后的结构化结果。
4. 原始输入在调用后保持不变。
5. 对非法输入给出明确错误。

建议提交信息：

```text
feat: implement day 1 detection cleaner
```

---

## 38. 进入 Day 2 前的标准

如果你能够不查答案完成下面的任务，就可以进入 Day 2：

> 给定一批嵌套字典形式的模型识别结果，在不修改输入的前提下，完成字段校验、白名单过滤、置信度过滤、坐标转换、面积计算、去重统计和排序，并能解释每一步是否创建了新对象。

Day 2 将在此基础上学习：

- dataclass
- `slots=True`
- `frozen=True`
- 类型标注
- `numpy.typing.NDArray`
- 模块与包的职责划分

目标是把今天使用的“松散字典”，升级为清晰、稳定、可检查的视觉数据模型。

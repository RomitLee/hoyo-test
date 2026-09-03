# 04. 事件识别与时序状态机

## 1. 单帧识别不等于事件识别

单帧模型只能告诉你“这一帧看到了什么”。用户想要的是“在什么时间发生了什么”，因此必须增加时间维度：

```text
单帧观察 → 连续帧去抖 → 状态变化 → 事件生成 → 去重与关联
```

例如背包按钮在 3 帧中都被检测到，不应输出 3 条“打开背包”，而应只输出一次 `inventory_opened`。

## 2. 推荐状态

```python
scene: str  # unknown, login, main, map, battle
panel: str  # none, inventory, task, dialogue
battle_active: bool
map_name: str | None
battle_id: str | None
last_event_timestamp_ms: int
```

状态机只接受经过置信度与连续帧确认的观察：

```text
unknown → main_menu → map
                     ├→ inventory_open
                     └→ battle
battle → battle_result → map
```

## 3. 地图名称识别

建议流程：

1. 先用场景/地图区域判断当前确实处于地图画面。
2. 只裁剪地图名称 ROI，不做整屏 OCR。
3. 放大、灰度化、阈值化或锐化。
4. OCR 读取候选文本。
5. 与地图白名单做归一化匹配。
6. 连续 2～3 个采样周期相同后生成 `map_entered`。

输出示例：

```json
{
  "type": "map_entered",
  "payload": {
    "map_name": "北俱芦洲",
    "raw_text": "北俱芦洲"
  },
  "confidence": 0.94
}
```

## 4. 背包与物品识别

分两路处理：

### 面板是否打开

- YOLO 检测背包面板特征区域。
- 固定位置模板匹配。
- 关键 UI 像素/边缘特征。

### 有哪些物品

- 检测物品格网格或固定 ROI。
- OCR 读取物品名称。
- 对识别结果做字典纠错。
- 记录格子位置、数量、名称和置信度。

不要只保存“背包有物品”这句话，要保存结构化清单：

```json
{
  "items": [
    {"name": "导标旗", "quantity": 2, "slot": [0, 0], "confidence": 0.91},
    {"name": "包子", "quantity": 15, "slot": [0, 1], "confidence": 0.97},
    {"name": "飞行符", "quantity": 3, "slot": [0, 2], "confidence": 0.95}
  ]
}
```

## 5. 战斗、技能和伤害

### 切入战斗

同时使用多个证据：

- 战斗 UI/操作栏检测。
- 战斗背景或敌方区域场景分类。
- 回合文字或按钮 OCR。
- 连续若干帧保持战斗布局。

满足条件后生成一次 `battle_started`，并分配 `battle_id`。

### 使用技能

需要定义可观测证据，例如：

- 技能按钮高亮变化。
- 技能图标从冷却/未选中变为选中。
- 角色动作/战斗日志出现技能名。
- 技能名 OCR 与时间窗口匹配。

仅凭一个图标闪烁可能无法证明技能真的释放，建议事件状态先标记为 `candidate`，等动画或战斗日志证据到达后升级为 `confirmed`。

### 伤害数字

- 只在战斗状态下调用伤害数字 OCR。
- 对数字 ROI 做放大、颜色筛选和连通域分割。
- 连续帧中相同数字要去重。
- 将伤害数字与最近的技能候选事件按时间窗口关联。

```text
skill_candidate at t=252000 ms
 damage_text at t=252180 ms
 → confirmed skill_used + damage_dealt
```

## 6. 事件去重

可使用以下键去重：

```text
(event_type, normalized_payload, time_bucket)
```

例如同一地图名在 3 秒内反复 OCR 到，不重复生成进入地图事件；但地图名从“北俱芦洲”变为“长寿郊外”时生成新事件。

## 7. 低置信度处理

不要强行输出一个确定答案：

```json
{
  "type": "map_entered",
  "status": "candidate",
  "confidence": 0.58,
  "alternatives": ["北俱芦洲", "北俱芦州"],
  "needs_review": true
}
```

后续可以做人工复核界面，用修正结果反哺训练集。

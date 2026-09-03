# 06. 数据结构与输出格式

## 1. 原始帧记录

```json
{
  "source_id": "capture_01",
  "frame_index": 5760,
  "timestamp_ms": 192000,
  "width": 1920,
  "height": 1080,
  "pixel_format": "bgr24",
  "saved_path": "evidence/capture_01_frame_00005760.jpg"
}
```

原始帧可以不全部保存，但元数据应保留。视频文件、证据图和 JSON 之间靠 `source_id + frame_index + timestamp_ms` 关联。

## 2. 观察记录

```json
{
  "source_id": "capture_01",
  "frame_index": 5760,
  "timestamp_ms": 192000,
  "kind": "ocr",
  "region": "map_name",
  "text": "北俱芦洲",
  "confidence": 0.96,
  "model": "paddleocr-v1",
  "bbox": [800, 42, 1040, 82]
}
```

建议每条观察都保留：

- 算法来源。
- 模型名称和版本。
- ROI 名称。
- 原始文本。
- 归一化文本。
- 置信度。
- 证据框。
- 帧号和时间戳。

## 3. 事件记录

```json
{
  "event_id": "evt_000042",
  "source_id": "capture_01",
  "timestamp_ms": 252000,
  "type": "damage_dealt",
  "status": "confirmed",
  "confidence": 0.93,
  "scene": "battle",
  "battle_id": "battle_0007",
  "payload": {
    "amount": 12445,
    "target": null,
    "skill_name": "横扫千军"
  },
  "evidence_frame_indices": [7560, 7561, 7562],
  "pipeline_version": "0.1.0"
}
```

## 4. 事件类型建议

```text
application_opened
main_scene_detected
map_entered
inventory_opened
inventory_closed
inventory_snapshot
battle_started
battle_round_started
skill_candidate
skill_used
damage_dealt
battle_ended
unknown_scene
```

`skill_candidate` 和 `skill_used` 不要混为一谈。前者是观察层推断，后者是经过时间关联和多证据确认的业务事件。

## 5. JSONL、SQLite 还是 Parquet

### JSONL：第一阶段

一行一条事件，适合实时追加：

```text
runtime/events/2026-09-03.jsonl
```

优点是简单、可追踪、方便 Git 外部工具读取；缺点是复杂查询不方便。

### SQLite：MVP 稳定后

建议表：

```text
sessions
frames
observations
events
inventory_items
model_runs
```

SQLite 适合单机应用和按时间、事件类型、地图、战斗 ID 查询。

### Parquet：大规模离线分析

当需要分析数百小时视频、训练统计或 pandas/Polars 查询时再使用 Parquet。不要在项目第一天引入它。

## 6. 时间戳要求

时间戳应明确是：

- 视频时间：相对视频起点的毫秒数。
- 采集时间：分析电脑的单调时钟。
- 墙上时间：用于日志展示的日期时间。

实时系统内部应使用单调时钟计算间隔，展示和落盘时再转换为可读时间。不要仅依赖处理完成时间，因为队列延迟会让事件看起来发生得更晚。

# 07. MVP 路线图与开发顺序

## 1. 阶段 0：确认采集链路（1～2 天）

目标：不使用任何 AI，也能稳定获得画面。

- 接好游戏电脑、采集卡和分析电脑。
- 确认系统能识别采集卡为摄像头或视频设备。
- 记录分辨率、FPS、色彩格式和延迟。
- 用 OpenCV 显示实时画面。
- 用 FFmpeg 录制 10 分钟测试视频。
- 验证断线、黑屏和重新连接时的错误处理。

验收：连续运行 30 分钟，能记录输入 FPS、丢帧和采集错误。

## 2. 阶段 1：视频与抽帧（2～3 天）

- 实现离线 MP4 读取。
- 实现 1/5/10 FPS 抽帧配置。
- 为每帧生成帧号和毫秒时间戳。
- 实现 ROI 裁剪与证据帧保存。
- 增加环形缓冲，支持保存事件前后片段。

验收：给定视频可以复现同一批帧和时间戳。

## 3. 阶段 2：先做规则和 OCR（3～7 天）

第一版不要急着训练 YOLO：

- 手工定义地图名、背包、战斗和伤害的 ROI。
- 用模板匹配或像素差检测 UI 开关。
- 对地图名和伤害数字调用 OCR。
- 建立文本清洗和白名单纠错。
- 输出 JSONL 观察记录。

验收：在一段短视频上能输出地图变化、背包打开、战斗开始等候选记录。

## 4. 阶段 3：标注并训练轻量 YOLO（1～2 周）

只标注稳定、可见、边界清晰的目标：

```text
inventory_panel
battle_panel
skill_icon
item_slot
map_name_region
damage_region
```

流程：

```text
抽帧 → 去重 → 标注 → 划分 train/val/test → 训练 → 评估 → 回放验证
```

注意：按“视频段”划分数据集，而不是随机把相邻帧分到训练和验证中，否则指标会虚高。

验收：在未见过的视频段中，关键 UI 的误报和漏报达到可接受水平，并能解释失败样本。

## 5. 阶段 4：时序事件引擎（1 周）

- 实现观察、状态、事件三层对象。
- 增加连续帧确认和冷却时间。
- 关联地图、背包、战斗和技能事件。
- 输出事件证据帧路径。
- 设计低置信度人工复核标记。

验收：同一事件不因连续帧重复输出，短暂闪烁和一次误检不会制造业务事件。

## 6. 阶段 5：离线回放与评估（1 周）

建立一个回放工具，逐帧显示：

- 原始画面。
- ROI。
- YOLO 框和置信度。
- OCR 原文与归一化文本。
- 当前状态。
- 已输出事件。

人工制作“真值事件表”，计算：

```text
事件召回率 = 正确识别事件数 / 真值事件数
事件精确率 = 正确识别事件数 / 输出事件数
时间误差 = 预测时间 - 真值时间
```

实时项目不能只看 mAP 或 OCR 字符准确率，最终要看业务事件是否正确。

## 7. 第一版建议交付物

```text
README.md
docs/
  01-project-definition.md
  02-architecture.md
  03-video-ingest-and-frame-extraction.md
  04-event-recognition.md
  05-model-selection.md
  06-data-schema.md
  07-roadmap-and-mvp.md
src/hoyo_analyzer/
  capture.py
  sampling.py
  perception.py
  ocr.py
  event_machine.py
  storage.py
  cli.py
tests/
```

## 8. 最小命令设计

未来 CLI 可以设计为：

```bash
# 离线抽帧
python -m hoyo_analyzer extract-frames input.mp4 --fps 5

# 离线分析
python -m hoyo_analyzer analyze-video input.mp4 --config configs/default.toml

# 实时采集卡分析
python -m hoyo_analyzer live --device 0 --fps 5

# 回放并导出结果
python -m hoyo_analyzer report runtime/events/2026-09-03.jsonl
```

## 9. 不建议一开始做的事情

- 不要先训练一个“识别整段游戏视频”的大模型。
- 不要每帧调用视觉大模型 API。
- 不要先做自动鼠标键盘控制。
- 不要把所有物品和技能都设成 YOLO 类别。
- 不要把视频永久拆成全部原始图片。
- 不要在没有真值数据的情况下只看模型自测结果。

## 10. 第一周完成标准

第一周结束时，理想状态是：

```text
采集卡能稳定输入
→ 视频可复现抽帧
→ 关键 ROI 能保存
→ OCR 能读出少量文字
→ JSONL 能记录带时间戳的观察
→ 状态机能生成 3～4 类候选事件
```

做到这里，项目就从想法变成了可测量的 MVP。之后每增加一种事件，都按“数据样本 → 识别器 → 时序规则 → 真值评估 → 回放复盘”的闭环推进。

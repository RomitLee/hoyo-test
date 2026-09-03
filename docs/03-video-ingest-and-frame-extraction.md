# 03. HDMI 采集与视频拆帧

## 1. 硬件连接

```text
游戏电脑显卡 HDMI OUT
        ↓
视频采集卡 HDMI IN
        ↓ USB 3.0 / PCIe
分析电脑
```

如果需要同时让玩家看到画面，应使用显卡分屏、HDMI 分配器或采集卡的环出功能。采集卡的驱动、输入分辨率、刷新率和色彩格式需要先在系统中确认。

## 2. 离线视频：FFmpeg 抽帧

离线训练和调试推荐 FFmpeg，因为它适合稳定解码、裁剪时间段和批量导出：

```bash
# 每秒抽 5 张，输出 JPEG
ffmpeg -i input.mp4 -vf fps=5 -q:v 2 frames/%08d.jpg

# 每 200 毫秒抽一张，并保留从 00:10 开始的片段
ffmpeg -ss 00:00:10 -i input.mp4 -vf fps=5 -q:v 2 frames/%08d.jpg

# 抽取 PNG，适合需要无损像素对比的调试
ffmpeg -i input.mp4 -vf fps=5 frames/%08d.png
```

训练数据不建议全部使用 60 FPS 的相邻帧，因为相邻帧高度相似，会造成数据冗余和训练/验证集泄漏。可以先按 2～10 FPS 抽取，再针对转场、弹窗和技能动画补充关键片段。

## 3. 离线视频：Python/OpenCV 读取

```python
from pathlib import Path

import cv2


cap = cv2.VideoCapture("input.mp4")
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
frame_index = 0
sample_every = max(1, round(fps / 5.0))  # 目标约 5 FPS

while True:
    ok, frame = cap.read()
    if not ok:
        break
    if frame_index % sample_every == 0:
        timestamp_ms = frame_index / fps * 1000.0
        cv2.imwrite(
            str(Path("frames") / f"{frame_index:08d}_{timestamp_ms:.0f}.jpg"),
            frame,
        )
    frame_index += 1

cap.release()
```

这里的 `frame_index / fps` 只是离线文件的近似时间。对于严格同步，应优先使用 FFmpeg/解码器提供的 PTS。

## 4. 实时 HDMI：不要默认保存每帧图片

实时模式建议：

```text
采集帧 → 内存环形缓冲 → 抽样识别 → 只保存证据帧/事件前后片段
```

原因：1920×1080×3 的原始帧约 6 MB，60 FPS 连续保存会快速产生大量数据。更合理的策略是：

- 常态感知：5 FPS。
- 检测到 UI 变化：临时升到 10～15 FPS。
- 事件触发：保存事件前 2 秒、后 3 秒的 JPEG 或短视频。
- 调试模式：手动开启全帧保存，不作为默认生产模式。

## 5. 实时采集伪代码

```python
from collections import deque
from time import monotonic

import cv2

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 60)

pre_buffer: deque[tuple[float, object]] = deque(maxlen=120)  # 约 2 秒
next_process_at = 0.0
process_interval = 0.2  # 5 FPS

while True:
    ok, frame = cap.read()
    captured_at = monotonic()
    if not ok:
        continue

    pre_buffer.append((captured_at, frame))
    if captured_at < next_process_at:
        continue
    next_process_at = captured_at + process_interval

    # 只在这里提交给预处理/检测/ OCR 队列
    observation = perceive(frame, captured_at)
    events = event_machine.update(observation)
    if events:
        save_evidence(pre_buffer, frame, events)
```

实际项目中需要把 `perceive`、`event_machine` 和 `save_evidence` 替换为真实模块，并使用有界队列处理背压。

## 6. 图片格式选择

| 用途 | 格式 | 说明 |
|---|---|---|
| 训练/标注 | JPEG 高质量 | 体积较小，足够大多数检测任务 |
| 像素差异/模板调试 | PNG | 无损，但体积大 |
| 事件证据 | JPEG + 原始时间戳 | 便于人工回看 |
| 长时录制 | H.264/H.265 视频 | 不要把长时间流保存为单张图片 |

文件名应包含 `source_id`、帧号和时间戳，例如：

```text
capture_01_frame_00005760_ts_192000.jpg
```

## 7. 推荐方案

- **训练集制作**：OBS 或采集卡录制 MP4 → FFmpeg 按场景/时间抽帧 → 标注。
- **实时识别**：采集卡 → OpenCV/FFmpeg → 内存抽样 → YOLO/OCR → 状态机。
- **故障复盘**：保留原视频或事件短片 + JSONL，不要只保留抽出的图片。

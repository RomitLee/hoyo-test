# Python 与 NumPy 10 天逐日教程

> 总体规划：[Python 与 NumPy 10 天速学方案](../../Python与NumPy十天速学方案.md)

本目录面向有既往编程经验、希望快速进入 OpenCV、YOLO 和视觉数据处理的学习者。每一天都包含核心知识、视觉项目示例、动手任务和验收标准。

## 课程目录

| 天数 | 主题 | 主要交付物 |
|---|---|---|
| [Day 01](./Day01-Python对象模型容器与常用表达式.md) | 对象模型、容器和常用表达式 | 检测结果清洗器 |
| [Day 02](./Day02-dataclass类型标注与模块设计.md) | dataclass、类型标注与模块设计 | 视觉领域模型 |
| [Day 03](./Day03-异常日志生成器与pytest.md) | 异常、日志、生成器与 pytest | 可观测的帧处理器 |
| [Day 04](./Day04-ndarray-shape-dtype与内存.md) | ndarray、shape、dtype 与内存 | 数组检查与图像校验工具 |
| [Day 05](./Day05-索引切片ROI视图与副本.md) | 索引、切片、ROI、视图与副本 | 安全 ROI 裁剪器 |
| [Day 06](./Day06-broadcasting-axis与向量化.md) | broadcasting、axis 与向量化 | 批量像素和检测框处理函数 |
| [Day 07](./Day07-reshape-transpose与模型输入布局.md) | reshape、transpose 与模型输入布局 | 模型输入预处理器 |
| [Day 08](./Day08-检测框坐标系统.md) | 检测框坐标系统 | box_ops 坐标工具集 |
| [Day 09](./Day09-IoU与多帧稳定性.md) | IoU 与多帧稳定性 | IoU 矩阵和稳定目标判断器 |
| [Day 10](./Day10-NumPy视觉数据处理器综合项目.md) | NumPy 视觉数据处理器综合项目 | 可测试的批量视觉数据流水线 |

## 推荐学习节奏

```text
阅读目标与概念：30～45 分钟
运行并修改示例：45～60 分钟
完成当天任务：60～90 分钟
测试、复盘、提交：20～30 分钟
```

## 学习规则

1. 每个 NumPy 操作前先预测输出 `shape` 和 `dtype`。
2. 转换函数默认不修改输入，确需原地修改时必须明确说明。
3. 视觉数学必须覆盖空输入、越界、零面积和错误类型。
4. 每天至少形成一个可运行交付物和一次 Git 提交。
5. Day 10 验收通过后，再进入 OpenCV 阶段。

## 建议项目目录

```text
src/hoyo_vision/
├── __init__.py
├── models.py
├── exceptions.py
├── image_ops.py
├── box_ops.py
└── pipeline.py

tests/
├── test_models.py
├── test_image_ops.py
├── test_box_ops.py
└── test_pipeline.py

examples/
├── day01_detection_cleaner_demo.py
└── day10_pipeline_demo.py

benchmarks/
└── vectorization_benchmark.py
```

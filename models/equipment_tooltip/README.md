# YOLO装备属性浮窗模型

请将训练得到的 Ultralytics YOLO 最佳权重命名为 `best.pt` 并放在本目录：

```text
models/equipment_tooltip/best.pt
```

当前模型的类别必须包含：

```text
0: equipment_tooltip
1: inventory_panel
```

“装备识别”手动流程只使用 `equipment_tooltip` 类别：它会从整张梦幻西游截图中定位属性浮窗，再将浮窗裁剪给 OCR。`best.pt` 不随 Git 提交，请在本机训练完成后自行复制。

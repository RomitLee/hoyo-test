"""Split an inbox of YOLO images/labels into train, validation and test sets."""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 YOLO 原始数据划分为 train/val/test")
    parser.add_argument("--source", type=Path, default=Path("datasets/mhxy_ui_detection/inbox"))
    parser.add_argument("--output", type=Path, default=Path("datasets/mhxy_ui_detection"))
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--copy", action="store_true", help="复制文件；默认移动文件")
    return parser.parse_args()


def validate_label(label_path: Path) -> None:
    for line_number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"{label_path}:{line_number} 应有5列：class x_center y_center width height")
        if fields[0] not in {"0", "1"}:
            raise ValueError(f"{label_path}:{line_number} 类别必须是0 equipment_tooltip或1 inventory_panel")
        values = [float(value) for value in fields[1:]]
        if any(value < 0 or value > 1 for value in values):
            raise ValueError(f"{label_path}:{line_number} 坐标必须在0到1之间")
        if values[2] <= 0 or values[3] <= 0:
            raise ValueError(f"{label_path}:{line_number} width/height必须大于0")


def main() -> int:
    args = parse_args()
    if not 0 <= args.val_ratio < 1 or not 0 <= args.test_ratio < 1 or args.val_ratio + args.test_ratio >= 1:
        raise ValueError("val/test比例必须大于等于0，且两者之和小于1")
    source = args.source
    if not source.is_dir():
        raise FileNotFoundError(f"找不到原始数据目录：{source}")
    images = sorted(path for path in source.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        raise ValueError(f"{source} 中还没有图片")

    pairs: list[tuple[Path, Path | None]] = []
    for image in images:
        label = image.with_suffix(".txt")
        if label.exists():
            validate_label(label)
        pairs.append((image, label if label.exists() else None))

    shuffled = pairs[:]
    random.Random(args.seed).shuffle(shuffled)
    test_count = round(len(shuffled) * args.test_ratio)
    val_count = round(len(shuffled) * args.val_ratio)
    groups = {
        "test": shuffled[:test_count],
        "val": shuffled[test_count : test_count + val_count],
        "train": shuffled[test_count + val_count :],
    }
    operation = shutil.copy2 if args.copy else shutil.move
    for split, items in groups.items():
        image_dir = args.output / "images" / split
        label_dir = args.output / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        for image, label in items:
            operation(str(image), str(image_dir / image.name))
            if label is not None:
                operation(str(label), str(label_dir / label.name))
        print(f"{split}: {len(items)} 张图片")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

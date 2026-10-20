"""Evaluate panel recognition against user-labelled folders without changing the samples."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from hoyo_analyzer.inventory_fullness import InventoryFullnessDetector
from hoyo_analyzer.panels import PanelDetector

FOLDERS = {
    "打开背包": "inventory", "打开给予面板": "give", "打开给与面板": "give",
    "打开交易面板": "trade", "打开属性面板": "attributes", "摊位面板": "stall",
    "打开摊位面板": "stall", "摊位记录面板": "stall_records",
    "打开摊位记录面板": "stall_records", "默认游戏界面": "default",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("runtime/panel-evaluation"))
    parser.add_argument("--check-800", action="store_true", help="Also test native-size UI on an 800x600 canvas")
    args = parser.parse_args()
    detector, inventory = PanelDetector(), InventoryFullnessDetector()
    results = []
    for folder in sorted(args.samples.iterdir()):
        if not folder.is_dir() or folder.name not in FOLDERS:
            continue
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
                continue
            image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Cannot read sample: {path}")
            cases = [("native", image)]
            if args.check_800 and image.shape[:2] == (480, 640):
                canvas = np.zeros((600, 800, 3), dtype=np.uint8)
                canvas[60:540, 80:720] = image
                cases.append(("800x600_native_ui", canvas))
            for case, frame in cases:
                start = perf_counter()
                observed = detector.analyze(frame)
                result = {
                    "file": str(path), "case": case, "expected": FOLDERS[folder.name],
                    "predicted": observed.panel_type, "confidence": observed.confidence,
                    "correct": observed.panel_type == FOLDERS[folder.name],
                    "milliseconds": (perf_counter() - start) * 1000,
                }
                if FOLDERS[folder.name] == "inventory":
                    analysis = inventory.analyze(frame)
                    result["inventory"] = analysis.to_dict()
                results.append(result)
    if not results:
        raise ValueError("No labelled samples found")
    errors = [row for row in results if not row["correct"]]
    report = {
        "samples": len(results), "correct": len(results) - len(errors), "errors": errors,
        "classes": dict(Counter(row["expected"] for row in results)),
        "average_milliseconds": sum(row["milliseconds"] for row in results) / len(results),
        "results": results,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"results", "errors"}}, ensure_ascii=True))
    for error in errors:
        print(json.dumps(error, ensure_ascii=True))
    inventory_rows = [row for row in results if "inventory" in row]
    print("inventory_states:", dict(Counter(row["inventory"]["status"] for row in inventory_rows)))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluate the rule-based inventory detector on the local screenshot folders."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from hoyo_analyzer.inventory_fullness import InventoryFullnessDetector

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path("runtime/inventory_samples"))
    parser.add_argument("--output", type=Path, default=Path("runtime/inventory_analysis"))
    parser.add_argument("--template", type=Path, default=Path("assets/templates/inventory_open.png"))
    parser.add_argument("--no-images", action="store_true", help="Do not save annotated screenshots")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    detector = InventoryFullnessDetector(args.template)
    rows: list[dict[str, object]] = []
    confusion: dict[str, Counter[str]] = defaultdict(Counter)

    for expected_dir in sorted(path for path in args.samples.iterdir() if path.is_dir()):
        for image_path in sorted(path for path in expected_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES):
            image = detector.read_image(image_path)
            if image is None:
                predicted = "invalid"
                analysis_dict: dict[str, object] = {"reason": "image could not be decoded"}
            else:
                analysis = detector.analyze(image)
                predicted = analysis.status.value
                analysis_dict = analysis.to_dict()
                if not args.no_images:
                    output_path = args.output / "annotated" / expected_dir.name / image_path.name
                    detector.write_image(output_path, detector.annotate(image, analysis))
            confusion[expected_dir.name][predicted] += 1
            rows.append(
                {
                    "file": str(image_path),
                    "expected": expected_dir.name,
                    "predicted": predicted,
                    "correct": predicted == expected_dir.name,
                    **analysis_dict,
                }
            )

    args.output.mkdir(parents=True, exist_ok=True)
    correct = sum(bool(row["correct"]) for row in rows)
    report = {
        "sample_count": len(rows),
        "correct_count": correct,
        "accuracy": correct / len(rows) if rows else 0.0,
        "confusion": {expected: dict(counts) for expected, counts in sorted(confusion.items())},
        "errors": [row for row in rows if not row["correct"]],
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output / "results.csv").open("w", encoding="utf-8-sig", newline="") as csv_file:
        fieldnames = [
            "file",
            "expected",
            "predicted",
            "correct",
            "confidence",
            "reason",
            "empty_count",
            "occupied_count",
            "unknown_count",
            "title_score",
            "location_method",
        ]
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Evaluated {len(rows)} images: {correct} correct ({report['accuracy']:.1%})")
    for expected, counts in sorted(confusion.items()):
        print(f"  {expected:16s} {dict(counts)}")
    print(f"Report: {args.output / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Train the Dream Westward Journey UI detector with Ultralytics YOLO."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_CONFIG = PROJECT_ROOT / "datasets" / "mhxy_ui_detection" / "dataset.yaml"
OUTPUT_DIR = PROJECT_ROOT / "runs" / "yolo"
MODEL_NAME = "yolo11n.pt"


def main() -> None:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "未安装 ultralytics，请先执行：.\\.venv\\Scripts\\python.exe -m pip install -e .[yolo]"
        ) from exc
    model = YOLO(MODEL_NAME)
    model.train(
        data=str(DATASET_CONFIG),
        epochs=100,
        imgsz=640,
        batch=8,
        device=0,
        workers=0,
        project=str(OUTPUT_DIR),
        name="mhxy_ui_detection_v1",
        pretrained=True,
        cache=False,
        patience=30,
    )


if __name__ == "__main__":
    main()

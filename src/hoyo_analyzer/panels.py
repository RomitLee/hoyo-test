"""Local title-template recognition and debounced foreground-panel transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from .models import BEIJING_TZ, Event
from .panel_types import PANEL_NAMES, panel_event_name
from .paths import resolve_application_path

TEMPLATE_NAMES = {
    **{key: (f"panel_{key}.png",) for key in PANEL_NAMES},
    "attributes": ("panel_attributes.png", "panel_attributes_master_skills.png",
                   "panel_attributes_auxiliary_skills.png", "panel_attributes_cultivation.png"),
    "default": ("panel_default.png",),
}


@dataclass(frozen=True)
class PanelObservation:
    panel_type: str  # unknown is distinct from a confirmed default game screen.
    confidence: float = 0.0
    bbox: tuple[int, int, int, int] | None = None


class PanelDetector:
    def __init__(self, template_directory: Path | None = None, threshold: float = 0.9) -> None:
        directory = template_directory or resolve_application_path("assets/templates")
        self.threshold = threshold
        self.templates: dict[str, list[np.ndarray]] = {}
        self._scaled: dict[tuple[str, int, float], tuple[np.ndarray, np.ndarray]] = {}
        for kind, filenames in TEMPLATE_NAMES.items():
            loaded = []
            for name in filenames:
                path = directory / name
                if not path.is_file():
                    continue
                template = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if template is not None:
                    loaded.append(template)
            self.templates[kind] = loaded

    def analyze(self, image: np.ndarray) -> PanelObservation:
        if image.ndim != 3 or image.shape[2] != 3 or not image.size:
            return PanelObservation("unknown")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Prefer native pixel-sized UI. Also support uniformly resized captures.
        scales = (1.0, min(image.shape[1] / 640, image.shape[0] / 480))
        default_match: PanelObservation | None = None
        for scale in dict.fromkeys(round(value, 3) for value in scales):
            matches: list[PanelObservation] = []
            for kind, templates in self.templates.items():
                for index, original in enumerate(templates):
                    key = (kind, index, scale)
                    if key not in self._scaled:
                        colour = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                        self._scaled[key] = colour, cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
                    colour, template = self._scaled[key]
                    height, width = template.shape
                    if height > gray.shape[0] or width > gray.shape[1]:
                        continue
                    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))
                    if score < self.threshold:
                        continue
                    patch = image[y:y + height, x:x + width]
                    # A red "摊位记录" button has the same letters as the blue
                    # title. Correlation alone ignores the background colour.
                    error = float(np.abs(patch.astype(np.int16) - colour.astype(np.int16)).mean())
                    if error <= 25:
                        matches.append(PanelObservation(kind, float(score), (x, y, x + width, y + height)))
            panels = [match for match in matches if match.panel_type != "default"]
            if panels:
                return max(panels, key=lambda match: match.confidence)
            if matches:
                candidate = max(matches, key=lambda match: match.confidence)
                if default_match is None or candidate.confidence > default_match.confidence:
                    default_match = candidate
        return default_match or PanelObservation("unknown")


class PanelTracker:
    def __init__(self, confirm_frames: int = 3, close_seconds: float = 1.0) -> None:
        self.confirm_frames = confirm_frames
        self.close_ms = int(close_seconds * 1000)
        self.stable: str | None = None
        self.reset_pending()

    def reset_pending(self) -> None:
        """Keep the last confirmed panel across a pause, discard partial hits."""
        self.candidate: str | None = None
        self.candidate_since = 0
        self.count = 0

    def update(self, observation: PanelObservation, timestamp_ms: int, frame_index: int = 0,
               occurred_at: datetime | None = None) -> list[Event]:
        kind = observation.panel_type
        if kind == "unknown":
            self.reset_pending()
            return []
        if kind != self.candidate or timestamp_ms < self.candidate_since:
            self.candidate, self.candidate_since, self.count = kind, timestamp_ms, 0
        self.count += 1
        if self.count < self.confirm_frames or kind == self.stable:
            return []
        if kind == "default" and timestamp_ms - self.candidate_since < self.close_ms:
            return []
        previous = self.stable
        self.stable = kind
        # Switching dialogs does not prove the previous one closed: it may be
        # behind the new dialog (e.g. stall records). Log only the observed action.
        event_type = "game_default" if kind == "default" else "panel_opened"
        now = (occurred_at or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
        message = "默认游戏界面" if kind == "default" and previous is None else panel_event_name(event_type, kind)
        return [Event(uuid4().hex, timestamp_ms, event_type, confidence=observation.confidence,
                      payload={"panel_type": None if kind == "default" else kind, "message": message,
                               "occurred_at": now.isoformat(timespec="milliseconds")},
                      evidence_frame_index=frame_index, beijing_time=now.strftime("%Y年%m月%d日%H:%M:%S"))]

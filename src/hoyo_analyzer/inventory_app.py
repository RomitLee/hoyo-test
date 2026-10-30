"""Minimal Windows desktop application dedicated to inventory monitoring."""

from __future__ import annotations

import os
import json
import re
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Lock
from time import monotonic, time as wall_time
from typing import Any, ClassVar
from uuid import uuid4

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

import cv2
import numpy as np

from .capture import make_source
from .config import AppConfig, load_config
from .event_machine import EventMachine
from .inventory_fullness import InventoryAnalysis, InventoryFullnessDetector, InventoryStatus
from .models import Event, Observation
from .money import MoneyDetector, MoneyTracker
from .panel_targets import PanelTargetDetector, attach_target, target_payload
from .panels import PanelDetector, PanelObservation, PanelTracker
from .paths import application_root, resolve_application_path
from .sampler import AdaptiveSampler, SamplingProfile
from .screenshots import EVENT_SCREENSHOT_PANELS, capture_panel_screenshot
from .stamina import StaminaDetector
from .stall_stock import StallStockDetector, StallStockTracker
from .stall_market import OwnStallMarketDetector, StallMarketDetector, StallVisitTracker
from .storage import JsonlEventSink
from .wgc import is_window_available, is_window_minimized, list_capturable_windows

try:
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QCloseEvent, QImage, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised by packaged startup handling
    raise RuntimeError("桌面界面需要 PySide6") from exc


APP_NAME = "梦幻西游背包监控"
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600
MAX_MONITORED_WINDOWS = 8

# Gem/name/price OCR takes several seconds on real 640x480 captures.  It must
# never run in the per-window capture loop, otherwise panel-open events wait
# behind OCR and multiple windows amplify the delay.  A small shared pool keeps
# the UI/capture threads responsive without letting OCR saturate the machine.
_MARKET_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hoyo-market-ocr")
_PANEL_DETAIL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hoyo-panel-detail")


@dataclass(frozen=True, slots=True)
class AccountInfo:
    secondary_region: str = "未识别"
    role_name: str = "未识别"
    role_id: str = ""


def parse_account_info(window_title: str) -> AccountInfo:
    """Extract secondary region and role data from the game window title."""

    # Real game titles use both Chinese and ASCII brackets, for example:
    # 梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])
    region_match = re.search(
        r"(?P<primary>[\w\u3400-\u9fff]+)\s*[\[【](?P<secondary>[^\[\]【】]+)[\]】]",
        window_title,
    )
    if region_match is None:
        return AccountInfo()

    secondary_region = region_match.group("secondary").strip() or "未识别"
    role_match = re.search(
        r"(?P<role>[\w\u3400-\u9fff]+)\s*[\[【](?P<role_id>\d{4,})[\]】]",
        window_title[region_match.end() :],
    )
    if role_match is None:
        return AccountInfo(secondary_region=secondary_region)
    return AccountInfo(
        secondary_region=secondary_region,
        role_name=role_match.group("role").strip() or "未识别",
        role_id=role_match.group("role_id"),
    )


def is_mhxy_game_window_title(window_title: str) -> bool:
    """Accept only titles identifying the real 梦幻西游 ONLINE game client."""

    normalized = re.sub(r"\s+", "", window_title).upper()
    return "梦幻西游ONLINE" in normalized


def _analysis_signal(analysis: InventoryAnalysis) -> dict[str, Any]:
    return {
        "active": analysis.status.value == "full",
        "status": analysis.status.value,
        "confidence": analysis.confidence,
        "reason": analysis.reason,
        "empty_count": analysis.empty_count,
        "occupied_count": analysis.occupied_count,
        "unknown_count": analysis.unknown_count,
        "grid_bbox": list(analysis.grid_bbox) if analysis.grid_bbox else None,
        "location_method": analysis.location_method,
    }


class InventoryMonitorWorker(QObject):
    """Capture one game window and emit only inventory-related results."""

    preview_ready = Signal(object)
    preview_for_window = Signal(int, object)
    analysis_ready = Signal(object)
    analysis_for_window = Signal(int, object)
    event_ready = Signal(object)
    event_for_window = Signal(int, object)
    stats_ready = Signal(object)
    stats_for_window = Signal(int, object)
    finished = Signal()
    finished_for_window = Signal(int)
    failed = Signal(str)
    failed_for_window = Signal(int, str)
    live_snapshot_ready = Signal(int, object, str, str)

    PREVIEW_INTERVAL_SECONDS = 0.15
    MAX_PREVIEW_EDGE = 560

    def __init__(self, hwnd: int, config: AppConfig, window_title: str = "") -> None:
        super().__init__()
        self.hwnd = hwnd
        self.config = config
        self.window_title = window_title
        self._stop_requested = ThreadEvent()
        self._pause_requested = ThreadEvent()
        self._source: Any | None = None
        self._snapshot_lock = Lock()
        self._snapshot_request: str | None = None
        self._event_screenshot_lock = Lock()
        self._enabled_event_screenshot_panels = frozenset(EVENT_SCREENSHOT_PANELS)
        self._market_lock = Lock()
        self._market_detector = StallMarketDetector()
        self._own_stall_detector = OwnStallMarketDetector()
        self._market_future: Future | None = None
        identity = parse_account_info(window_title).role_id or f"hwnd-{hwnd}"
        self._market_directory = application_root() / "runtime" / "market-ocr-queue" / identity
        self._market_manifest = self._market_directory / "queue.json"
        self._market_queue = self._load_market_queue()
        self._recognition_lock = Lock()
        self._recognition_recent: dict[str, tuple[tuple[tuple[str, int], ...], float]] = {}
        self._recognition_last_ok = 0.0
        self._market_settle_ms: int | None = None
        self._market_visit: dict[str, Any] | None = None
        self._visit_state: dict[str, dict[str, Any]] = {}
        self._finished_visits: set[str] = set()
        self._visit_traces: dict[str, dict[str, Any]] = {}
        self._own_pending_sig: bytes | None = None
        self._own_stable = 0
        self._own_active_sig: bytes | None = None
        self._own_last_enqueue_ms: int | None = None
        self._detail_futures: set[Future] = set()

    def request_live_snapshot(self, request_id: str = "") -> None:
        """Request the next genuine game frame from the capture thread."""
        with self._snapshot_lock:
            if self._snapshot_request is None or request_id:
                self._snapshot_request = request_id

    def set_paused(self, paused: bool) -> None:
        """Pause analysis without tearing down the window capture session."""

        if paused:
            self._pause_requested.set()
        else:
            self._pause_requested.clear()

    def set_event_screenshot_panels(self, panels) -> None:
        """Update which event panel types are allowed to create screenshots."""

        with self._event_screenshot_lock:
            self._enabled_event_screenshot_panels = frozenset(
                panel for panel in panels if panel in EVENT_SCREENSHOT_PANELS
            )

    def _event_screenshot_panels(self) -> frozenset[str]:
        with self._event_screenshot_lock:
            return self._enabled_event_screenshot_panels

    def stop(self) -> None:
        self._stop_requested.set()
        if self._source is not None:
            self._source.close()

    def _reset_market_analysis(self) -> None:
        """Panel transitions must not discard already captured merchant visits."""

    def _load_market_queue(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self._market_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        return [item for item in raw if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("file"), str)
                and Path(item["file"]).name == item["file"]
                and item["file"].endswith(".png")
                and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 4
                and item.get("panel_type", "stall_other_buy") in {"stall_other_buy", "stall_buy"}]

    def _save_market_queue_locked(self) -> None:
        self._market_directory.mkdir(parents=True, exist_ok=True)
        temporary = self._market_manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._market_queue, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._market_manifest)

    @staticmethod
    def _own_stall_signature(image: Any, panel: PanelObservation) -> bytes | None:
        if panel.bbox is None:
            return None
        x, y, right, bottom = (int(value) for value in panel.bbox)
        bottom = min(bottom, image.shape[0])
        right = min(right, image.shape[1])
        if right - x < 20 or bottom - y < 20:
            return None
        field = image[max(0, y):bottom, max(0, x):right]
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (48, 32), interpolation=cv2.INTER_AREA).tobytes()

    @staticmethod
    def _own_signature_differs(left: bytes | None, right: bytes | None) -> bool:
        if left is None or right is None:
            return left is not right
        changed = sum(1 for a, b in zip(left, right) if abs(a - b) > 8)
        return changed >= max(24, len(left) // 12)

    def _enqueue_market_visit(self, image: Any, panel: PanelObservation,
                              timestamp_ms: int, frame_index: int,
                              visit_id: str = "", attempt: int = 0) -> None:
        if panel.bbox is None:
            return
        entry_id = uuid4().hex
        filename = f"{entry_id}.png"
        try:
            self._market_directory.mkdir(parents=True, exist_ok=True)
            encoded, data = cv2.imencode(".png", image)
            if not encoded:
                return
            (self._market_directory / filename).write_bytes(data.tobytes())
            entry = {
                "id": entry_id, "file": filename, "bbox": list(panel.bbox),
                "panel_type": panel.panel_type, "visit_id": visit_id or entry_id,
                "attempt": attempt,
                "timestamp_ms": timestamp_ms, "frame_index": frame_index,
                "occurred_at": datetime.now(UTC).isoformat(timespec="milliseconds"), "attempts": 0,
            }
            with self._market_lock:
                self._market_queue.append(entry)
                state = self._visit_state.setdefault(entry["visit_id"], {
                    "pending": 0, "captured": 0, "best": None, "best_task": None, "files": [],
                })
                state["pending"] += 1
                state["captured"] += 1
                state["files"].append(filename)
                trace = self._visit_traces.get(entry["visit_id"])
                if trace is not None:
                    trace["captures"].append({
                        "attempt": attempt,
                        "at": wall_time(),
                        "at_iso": entry["occurred_at"],
                        "frame": frame_index,
                        "bytes": len(data.tobytes()),
                    })
                self._save_market_queue_locked()
                if self._market_future is None:
                    self._submit_market_ocr_locked()
        except (OSError, cv2.error):
            try:
                (self._market_directory / filename).unlink(missing_ok=True)
            except OSError:
                pass

    def _start_market_ocr_queue(self) -> None:
        with self._market_lock:
            if self._market_future is None:
                self._submit_market_ocr_locked()

    def _submit_market_ocr_locked(self) -> None:
        if not self._market_queue or self._stop_requested.is_set():
            return
        task = dict(self._market_queue[0])
        future = _MARKET_OCR_EXECUTOR.submit(self._run_market_ocr, task)
        future.market_task = task  # type: ignore[attr-defined]
        self._market_future = future
        future.add_done_callback(self._market_ocr_finished)

    def _run_market_ocr(self, task: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        task["ocr_started_at"] = wall_time()
        path = self._market_directory / Path(task["file"]).name
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError("queued market screenshot is unreadable")
        panel_type = task.get("panel_type", "stall_other_buy")
        panel = PanelObservation(panel_type, 1.0, tuple(int(value) for value in task["bbox"]))
        detector = self._own_stall_detector if panel_type == "stall_buy" else self._market_detector
        reading = detector.analyze(image, panel)
        task["ocr_finished_at"] = wall_time()
        return task, reading

    def _market_ocr_finished(self, future: Future) -> None:
        task: dict[str, Any] | None = getattr(future, "market_task", None)
        reading = None
        failed = False
        try:
            task, reading = future.result()
        except Exception:  # noqa: BLE001 - optional OCR must not interrupt monitoring
            failed = True

        quotes_event: Event | None = None
        recognition: Event | None = None
        with self._market_lock:
            self._market_future = None
            current = self._market_queue[0] if self._market_queue else None
            if task is not None:
                task.setdefault("ocr_finished_at", wall_time())
            if current is not None and (task is None or current["id"] == task["id"]):
                visit_id = str(current.get("visit_id") or current["id"])
                if visit_id in self._finished_visits:
                    self._market_queue.pop(0)
                    try:
                        (self._market_directory / Path(current["file"]).name).unlink(missing_ok=True)
                    except OSError:
                        pass
                    if not self._stop_requested.is_set():
                        self._submit_market_ocr_locked()
                    return
                state = self._visit_state.setdefault(visit_id, {
                    "pending": 1, "captured": 1, "best": None, "best_task": None,
                    "files": [current["file"]],
                })
                if failed:
                    current["attempts"] = int(current.get("attempts", 0)) + 1
                    if current["attempts"] < 3:
                        self._save_market_queue_locked()
                        task = None
                    else:
                        self._market_queue.pop(0)
                        state["pending"] = max(0, state["pending"] - 1)
                else:
                    self._market_queue.pop(0)
                    state["pending"] = max(0, state["pending"] - 1)
                    trace = self._visit_traces.get(visit_id)
                    if trace is not None and current.get("panel_type") != "stall_buy":
                        started = current.get("ocr_started_at")
                        finished = current.get("ocr_finished_at")
                        trace["ocr"].append({
                            "attempt": current.get("attempt", 0),
                            "queued_at": wall_time(),
                            "started_at": started,
                            "finished_at": finished,
                            "wait_ms": round((started - wall_time()) * 1000) if started else None,
                            "duration_ms": round((finished - started) * 1000) if started and finished else None,
                            "quotes": len(getattr(reading, "quotes", ()) or ()),
                            "owner": getattr(reading, "owner_id", "") or "",
                            "failed": failed,
                        })
                    # Both own and other stalls need their best OCR frame kept;
                    # only the recognition-trace bookkeeping above is exclusive
                    # to other-player stalls.
                    if reading is not None:
                        best = state.get("best")
                        if best is None or len(reading.quotes) > len(best.quotes):
                            state["best"] = reading
                            state["best_task"] = dict(current)
                best = state.get("best")
                ready = (best is not None and best.quotes and str(getattr(best, "owner_id", "") or ""))
                if state["pending"] == 0 or (ready and not state.get("done")):
                    state["done"] = True
                    self._finished_visits.add(visit_id)
                    if len(self._finished_visits) > 64:
                        self._finished_visits.pop()
                    self._market_queue = [item for item in self._market_queue
                                          if str(item.get("visit_id")) != visit_id]
                    quotes_event, recognition = self._finish_market_visit_locked(visit_id, state)
                self._save_market_queue_locked()
                if task is not None:
                    try:
                        (self._market_directory / Path(current["file"]).name).unlink(missing_ok=True)
                    except OSError:
                        pass
            if not self._stop_requested.is_set():
                self._submit_market_ocr_locked()

        if quotes_event is not None:
            account = parse_account_info(self.window_title)
            quotes_event.payload.setdefault("window_hwnd", self.hwnd)
            quotes_event.payload.setdefault("window_title", self.window_title)
            quotes_event.payload.setdefault("secondary_region", account.secondary_region)
            quotes_event.payload.setdefault("role_name", account.role_name)
            quotes_event.payload.setdefault("role_id", account.role_id)
            self.event_ready.emit(quotes_event)
            self.event_for_window.emit(self.hwnd, quotes_event)
        if recognition is not None:
            recognition.payload.setdefault("window_hwnd", self.hwnd)
            recognition.payload.setdefault("window_title", self.window_title)
            account = parse_account_info(self.window_title)
            recognition.payload.setdefault("secondary_region", account.secondary_region)
            recognition.payload.setdefault("role_name", account.role_name)
            recognition.payload.setdefault("role_id", account.role_id)
            self._emit_recognition(recognition)

    def _finish_market_visit_locked(self, visit_id: str, state: dict[str, Any]) -> tuple[Event | None, Event | None]:
        """Build one quote event plus one recognition record for the whole visit."""
        best = state.get("best")
        best_task = state.get("best_task") or {}
        captured = int(state.get("captured", 1))
        owner = str(getattr(best, "owner_id", "") or "")
        quotes = [{"gem_name": name, "price_coins": price} for name, price in
                  (best.quotes if best is not None else [])]
        occurred_at = best_task.get("occurred_at") or datetime.now(UTC).isoformat(timespec="milliseconds")
        timestamp_ms = int(best_task.get("timestamp_ms") or 0)
        frame_index = int(best_task.get("frame_index") or 0)

        own_stall = str(best_task.get("panel_type") or "") == "stall_buy"
        quotes_event = None
        if quotes and own_stall:
            # Own stall prices have no merchant id; they must still reach the
            # console, otherwise editing a price never updates the web page.
            quotes_event = Event(
                uuid4().hex, timestamp_ms, "own_stall_gem_quotes",
                confidence=best.confidence,
                payload={"quotes": quotes, "occurred_at": occurred_at},
                evidence_frame_index=frame_index,
            )
        # Without a readable owner the quote cannot be attributed to a merchant;
        # keep it in the recognition log instead of dropping the whole visit.
        elif quotes and owner:
            quotes_event = Event(
                uuid4().hex, timestamp_ms, "market_gem_quotes",
                confidence=best.confidence,
                payload={"quotes": quotes, "occurred_at": occurred_at, "stall_owner_id": owner},
                evidence_frame_index=frame_index,
            )
        evidence_file = ""
        if best is not None and best_task:
            evidence_file = self._save_evidence_frame(best_task.get("file", ""))
        status = "ok" if quotes else ("short" if captured < 3 else "empty")
        trace = self._visit_traces.pop(visit_id, None)
        if trace is not None:
            trace["finalize_at"] = wall_time()
            trace["chosen_attempt"] = int(best_task.get("attempt", 0) or 0)
            trace["captured"] = captured
            trace["status"] = status
            trace["quote_count"] = len(quotes)
            trace["evidence_file"] = Path(evidence_file).name if evidence_file else ""
            if evidence_file:
                trace["evidence_at"] = wall_time()
        if own_stall:
            return quotes_event, None
        recognition = Event(
            uuid4().hex, timestamp_ms, "market_recognition",
            confidence=best.confidence if best is not None else 0.0,
            payload={
                "occurred_at": occurred_at,
                "stall_owner_id": owner,
                "quotes": quotes,
                "status": status,
                "evidence_file": evidence_file,
                "attempts": captured,
                "trace": trace,
            }, evidence_frame_index=frame_index,
        )
        for name in state.get("files", []):
            try:
                (self._market_directory / Path(name).name).unlink(missing_ok=True)
            except OSError:
                pass
        self._visit_state.pop(visit_id, None)
        return quotes_event, recognition

    def _save_evidence_frame(self, filename: str) -> str:
        """Keep the exact OCR frame as a JPEG for the recognition log."""
        if not filename:
            return ""
        source = self._market_directory / Path(filename).name
        try:
            image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                return ""
            encoded, data = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not encoded:
                return ""
            target_dir = application_root() / "runtime" / "screenshot-outbox"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{uuid4().hex}.jpg"
            target.write_bytes(data.tobytes())
            return str(target)
        except (OSError, cv2.error):
            return ""

    def _emit_recognition(self, event: Event) -> None:
        """Keep one useful record per stall visit and drop transition frames."""
        quotes = tuple(sorted((str(item["gem_name"]), int(item["price_coins"]))
                              for item in event.payload.get("quotes") or []))
        owner = str(event.payload.get("stall_owner_id") or "")
        now = monotonic()
        with self._recognition_lock:
            if not quotes:
                # A frame captured mid-fade: ignore when this stall just read.
                if now - self._recognition_last_ok < 8:
                    return
                event.payload["status"] = "empty"
            else:
                previous = self._recognition_recent.get(owner)
                if previous is not None:
                    previous_quotes, previous_at = previous
                    if now - previous_at < 120 and (
                        quotes == previous_quotes or set(quotes) <= set(previous_quotes)
                    ):
                        return
                self._recognition_recent[owner] = (quotes, now)
                self._recognition_last_ok = now
                event.payload["status"] = "ok"
                if len(self._recognition_recent) > 32:
                    oldest = min(self._recognition_recent,
                                 key=lambda key: self._recognition_recent[key][1])
                    self._recognition_recent.pop(oldest, None)
        self.event_for_window.emit(self.hwnd, event)

    def _schedule_panel_detail(self, event: Event, image: Any, panel: PanelObservation) -> None:
        """Run attributes/recipient OCR after the fast panel event has been confirmed."""
        panel_type = event.payload.get("panel_type")
        if panel_type not in {"attributes", "give", "trade"}:
            return
        task = (event.event_id, panel_type, event.payload.get("occurred_at"),
                event.timestamp_ms, event.evidence_frame_index or 0, image.copy(), panel)
        future = _PANEL_DETAIL_EXECUTOR.submit(self._run_panel_detail, task)
        self._detail_futures.add(future)
        future.add_done_callback(self._panel_detail_finished)

    def _run_panel_detail(self, task: tuple[Any, ...]) -> tuple[tuple[Any, ...], Any]:
        event_id, panel_type, occurred_at, timestamp_ms, frame_index, image, panel = task
        detector = StaminaDetector() if panel_type == "attributes" else PanelTargetDetector()
        return task, detector.analyze(image, panel)

    def _panel_detail_finished(self, future: Future) -> None:
        self._detail_futures.discard(future)
        try:
            task, reading = future.result()
        except Exception:  # noqa: BLE001 - optional details never block panel monitoring
            return
        if reading is None or self._stop_requested.is_set():
            return
        parent_id, panel_type, occurred_at, timestamp_ms, frame_index, _image, _panel = task
        if panel_type == "attributes":
            event = Event(
                parent_id, timestamp_ms, "panel_opened", confidence=reading.confidence,
                payload={
                    "panel_type": "attributes", "message": "打开属性面板",
                    "occurred_at": occurred_at, "supplemental": True,
                    "stamina_current": reading.current, "stamina_max": reading.maximum,
                    "character_level": reading.level,
                    "stamina_observed_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                }, evidence_frame_index=frame_index,
            )
        elif reading.identity is not None:
            name = "给予" if panel_type == "give" else "交易"
            event = Event(
                uuid4().hex, timestamp_ms, "panel_target_changed", confidence=reading.confidence,
                payload={
                    "panel_type": panel_type, "message": f"{name}对象：{reading.name}，{reading.level}级",
                    "occurred_at": occurred_at, "supplemental": True,
                    **target_payload(reading),
                }, evidence_frame_index=frame_index,
            )
        else:
            return
        account = parse_account_info(self.window_title)
        event.payload.update({
            "window_hwnd": self.hwnd, "window_title": self.window_title,
            "secondary_region": account.secondary_region, "role_name": account.role_name,
            "role_id": account.role_id,
        })
        self.event_ready.emit(event)
        self.event_for_window.emit(self.hwnd, event)

    @staticmethod
    def _preview(image: Any) -> Any:
        height, width = image.shape[:2]
        scale = min(1.0, InventoryMonitorWorker.MAX_PREVIEW_EDGE / max(height, width))
        if scale == 1.0:
            return image.copy()
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)

    @Slot()
    def run(self) -> None:
        sink: JsonlEventSink | None = None
        try:
            self._source = make_source(
                "windows-graphics-capture",
                self.config.capture.path,
                0,
                f"hwnd-{self.hwnd}",
                window_hwnd=self.hwnd,
            )
            template_path = resolve_application_path(self.config.inventory_monitor.template_path)
            detector = InventoryFullnessDetector(template_path)
            panel_detector = PanelDetector()
            panel_tracker = PanelTracker()
            self._start_market_ocr_queue()
            money_detector = MoneyDetector()
            money_tracker = MoneyTracker()
            stall_stock_detector = StallStockDetector()
            stall_stock_tracker = StallStockTracker()
            stall_visit_tracker = StallVisitTracker()
            money_character_id = parse_account_info(self.window_title).role_id
            last_panel_frame_ms: int | None = None
            sampler = AdaptiveSampler(
                SamplingProfile(
                    self.config.sampling.normal_fps,
                    self.config.sampling.normal_fps,
                    self.config.sampling.normal_fps,
                    0,
                )
            )
            machine = EventMachine(rules=(), inventory_monitor=self.config.inventory_monitor)
            event_log_path = resolve_application_path(self.config.output.event_log)
            sink = JsonlEventSink(event_log_path)
            analyzed = 0
            last_preview = 0.0
            last_stats = 0.0
            started = monotonic()
            was_paused = self._pause_requested.is_set()

            for captured, packet in enumerate(self._source, start=1):
                if self._stop_requested.is_set():
                    break
                if not is_window_minimized(self.hwnd):
                    with self._snapshot_lock:
                        request_id = self._snapshot_request
                        self._snapshot_request = None
                    if request_id is not None:
                        image = packet.image
                        if max(image.shape[:2]) > 1280:
                            scale = 1280 / max(image.shape[:2])
                            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                        encoded, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
                        if encoded:
                            self.live_snapshot_ready.emit(
                                self.hwnd, jpeg.tobytes(),
                                datetime.now(UTC).isoformat(timespec="milliseconds"), request_id,
                            )
                if self._pause_requested.is_set():
                    was_paused = True
                    continue
                if was_paused:
                    panel_tracker.reset_pending()
                    money_tracker.reset_pending()
                    stall_stock_tracker.reset()
                    self._reset_market_analysis()
                    stall_visit_tracker.reset()
                    # Never carry a partly confirmed warning across a manual
                    # pause. Resumed monitoring starts a fresh confirmation.
                    sampler = AdaptiveSampler(
                        SamplingProfile(
                            self.config.sampling.normal_fps,
                            self.config.sampling.normal_fps,
                            self.config.sampling.normal_fps,
                            0,
                        )
                    )
                    machine = EventMachine(rules=(), inventory_monitor=self.config.inventory_monitor)
                    last_preview = 0.0
                    was_paused = False
                # A minimized game often stops presenting new frames. Even if a
                # driver keeps sending cached frames, never treat them as a live
                # inventory result.
                if is_window_minimized(self.hwnd):
                    panel_tracker.reset_pending()
                    money_tracker.reset_pending()
                    stall_stock_tracker.reset()
                    self._reset_market_analysis()
                    stall_visit_tracker.reset()
                    continue
                now = monotonic()
                if now - last_preview >= self.PREVIEW_INTERVAL_SECONDS:
                    last_preview = now
                    preview = self._preview(packet.image)
                    self.preview_ready.emit(preview)
                    self.preview_for_window.emit(self.hwnd, preview)
                if sampler.accept(packet):
                    analyzed += 1
                    panel = panel_detector.analyze(packet.image)
                    if last_panel_frame_ms is not None and packet.timestamp_ms - last_panel_frame_ms > 1500:
                        panel_tracker.reset_pending()
                        stall_stock_tracker.reset()
                        self._reset_market_analysis()
                        stall_visit_tracker.reset()
                    last_panel_frame_ms = packet.timestamp_ms
                    panel_events = panel_tracker.update(panel, packet.timestamp_ms, packet.frame_index)
                    stall_panel = (panel.panel_type if panel.panel_type == panel_tracker.stable
                                   and panel.panel_type in {"stall", "stall_buy"} else None)
                    if panel.panel_type not in {"inventory", "unknown"}:
                        analysis = InventoryAnalysis(InventoryStatus.CLOSED, panel.confidence, "another panel is foreground")
                    else:
                        analysis = detector.analyze(packet.image)
                    current_character_id = parse_account_info(self.window_title).role_id
                    if money_character_id != current_character_id:
                        money_tracker = MoneyTracker()
                        stall_stock_tracker.reset()
                        self._reset_market_analysis()
                        stall_visit_tracker.reset()
                        money_character_id = current_character_id
                    signal = _analysis_signal(analysis)
                    signal["panel_type"] = panel.panel_type
                    signal["stall_panel_type"] = stall_panel
                    signal["sell_stock_count"] = stall_stock_tracker.update(
                        stall_stock_detector.count(packet.image, panel) if stall_panel == "stall" else None,
                        packet.timestamp_ms,
                    )
                    visit_events = stall_visit_tracker.update(
                        stall_visit_tracker.signature(self._market_detector, packet.image, panel),
                        packet.timestamp_ms, packet.frame_index)
                    # A fully confirmed default screen makes PanelTracker emit
                    # panel_opened on the same third frame as the reopen
                    # tracker.  Keep only the canonical panel event in that
                    # case; fast close/reopen flows retain visit_events.
                    if visit_events and any(
                        event.type == "panel_opened" and
                        event.payload.get("panel_type") == "stall_other_buy"
                        for event in panel_events
                    ):
                        visit_events = []
                    if any(
                        event.payload.get("panel_type") == "stall_other_buy"
                        and event.type in {"panel_opened", "market_stall_changed"}
                        for event in panel_events + visit_events
                    ) and self._market_visit is None:
                        # Three captures per visit: the first stable frame plus
                        # two retries, so a slow fade-in or a fast page flip
                        # cannot turn a readable stall into an empty result.
                        self._market_visit = {"id": uuid4().hex, "start_ms": packet.timestamp_ms, "attempt": 0}
                        self._visit_traces[self._market_visit["id"]] = {
                            "visit_id": self._market_visit["id"],
                            "panel_at": wall_time(),
                            "captures": [],
                            "ocr": [],
                        }
                    visit = self._market_visit
                    if visit is not None:
                        if panel.panel_type != "stall_other_buy":
                            self._market_visit = None
                        elif (visit["attempt"] < 3
                              and packet.timestamp_ms - visit["start_ms"] >= 400 * (visit["attempt"] + 1)):
                            self._enqueue_market_visit(
                                packet.image, panel, packet.timestamp_ms, packet.frame_index,
                                visit["id"], visit["attempt"],
                            )
                            visit["attempt"] += 1
                            if visit["attempt"] >= 3:
                                self._market_visit = None
                    if panel.panel_type == "stall_buy" and panel_tracker.stable == "stall_buy":
                        signature = self._own_stall_signature(packet.image, panel)
                        if signature is not None:
                            if self._own_signature_differs(signature, self._own_pending_sig):
                                self._own_pending_sig, self._own_stable = signature, 1
                            else:
                                self._own_stable += 1
                            throttled = (self._own_last_enqueue_ms is not None
                                         and packet.timestamp_ms - self._own_last_enqueue_ms < 3000)
                            changed = (self._own_stable >= 2
                                       and self._own_signature_differs(signature, self._own_active_sig))
                            fallback = (self._own_last_enqueue_ms is None
                                        or packet.timestamp_ms - self._own_last_enqueue_ms >= 30_000)
                            if (changed or fallback) and not throttled:
                                self._own_active_sig = signature
                                self._own_last_enqueue_ms = packet.timestamp_ms
                                self._enqueue_market_visit(
                                    packet.image, panel, packet.timestamp_ms, packet.frame_index
                                )
                    elif panel.panel_type != "stall_buy":
                        self._own_pending_sig = None
                        self._own_stable = 0
                        self._own_active_sig = None
                        self._own_last_enqueue_ms = None
                    if any(
                        event.type == "panel_opened" and event.payload.get("panel_type") == "stall_buy"
                        for event in panel_events
                    ):
                        self._enqueue_market_visit(
                            packet.image, panel, packet.timestamp_ms, packet.frame_index
                        )
                    signal["character_id"] = current_character_id
                    # Expensive OCR runs only after the base panel event is
                    # emitted.  Existing confirmed stamina may remain visible
                    # while a fresh attributes reading is pending.
                    target_events: list[Event] = []
                    signal.update(target_payload(None))
                    signal["target_reason"] = (
                        "后台识别中" if panel.panel_type in {"give", "trade"} else "未定位给予或交易面板"
                    )
                    for action in panel_events:
                        if action.type == "panel_opened" and panel.panel_type in {"give", "trade"}:
                            attach_target(action, None)

                    money = money_detector.read_fields(
                        packet.image, panel.bbox if panel.panel_type == "inventory" else None,
                        analysis.grid_bbox if panel.panel_type in {"inventory", "unknown"} else None,
                    )
                    signal["money_reason"] = money_detector.reason
                    signal["cash_detected"] = money[0] is not None
                    signal.update(money_tracker.update(money, packet.timestamp_ms))
                    self.analysis_ready.emit(signal)
                    self.analysis_for_window.emit(self.hwnd, signal)
                    observation = Observation(
                        packet.frame_index,
                        packet.timestamp_ms,
                        {"inventory_fullness": signal},
                        analysis.confidence,
                        "inventory-monitor",
                    )
                    for event in panel_events + target_events + visit_events + machine.update(observation):
                        account = parse_account_info(self.window_title)
                        event.payload.setdefault("window_hwnd", self.hwnd)
                        event.payload.setdefault("window_title", self.window_title)
                        event.payload.setdefault("secondary_region", account.secondary_region)
                        event.payload.setdefault("role_name", account.role_name)
                        event.payload.setdefault("role_id", account.role_id)
                        capture_panel_screenshot(event, packet.image, self._event_screenshot_panels())
                        sink.write(event)
                        if event.type in {"panel_opened", "panel_closed", "game_default", "panel_target_changed"}:
                            text_path = event_log_path.with_name("panel_events.log")
                            with text_path.open("a", encoding="utf-8") as handle:
                                handle.write(f"{event.display_time}{event.display_name}  "
                                             f"[{account.secondary_region}/{account.role_name}]\n")
                        self.event_ready.emit(event)
                        self.event_for_window.emit(self.hwnd, event)
                        if event.type == "panel_opened":
                            self._schedule_panel_detail(event, packet.image, panel)
                if now - last_stats >= 1.0:
                    last_stats = now
                    elapsed = max(0.001, now - started)
                    stats = {
                        "captured": captured,
                        "analyzed": analyzed,
                        "capture_fps": captured / elapsed,
                        "analysis_fps": analyzed / elapsed,
                    }
                    self.stats_ready.emit(stats)
                    self.stats_for_window.emit(self.hwnd, stats)
        except Exception as exc:  # noqa: BLE001 - surface packaged/native failures in the UI
            message = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            self.failed.emit(message)
            self.failed_for_window.emit(self.hwnd, message)
        finally:
            if self._source is not None:
                self._source.close()
            if sink is not None:
                sink.close()
            self.finished.emit()
            self.finished_for_window.emit(self.hwnd)


class LegacyInventoryMonitorWindow(QMainWindow):
    """Single-purpose inventory monitoring interface."""

    STATUS_PRESENTATION: ClassVar[dict[str, tuple[str, str, str]]] = {
        "full": ("背包已满", "#b91c1c", "#fee2e2"),
        "almost_full": ("背包接近满", "#b45309", "#fef3c7"),
        "not_full": ("背包未满", "#15803d", "#dcfce7"),
        "inventory_closed": ("背包未打开", "#b45309", "#fff7ed"),
        "invalid": ("背包被遮挡，无法检测", "#9f1239", "#ffe4e6"),
    }

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        root = application_root()
        path = config_path or root / "configs" / "default.toml"
        self.config = load_config(path if path.exists() else None)
        self.thread: QThread | None = None
        self.worker: InventoryMonitorWorker | None = None
        self.alerts: set[QMessageBox] = set()
        self._window_titles: dict[int, str] = {}
        self._auto_started_hwnd: int | None = None
        self._window_minimized = False
        self._build_ui()
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.refresh_windows)
        self.scan_timer.start(3000)
        self.window_state_timer = QTimer(self)
        self.window_state_timer.timeout.connect(self._poll_active_window_state)
        self.window_state_timer.start(1000)
        QTimer.singleShot(0, self.refresh_windows)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet(
            "QWidget{font-size:13px;} QGroupBox{font-weight:700;border:1px solid #dbe3ee;"
            "border-radius:8px;margin-top:8px;padding:10px;} QGroupBox::title{subcontrol-origin:margin;left:10px;}"
        )
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        title = QLabel("梦幻西游背包监控")
        title.setStyleSheet("font-size:24px;font-weight:800;color:#1e293b;")
        subtitle = QLabel("只检测：游戏窗口、背包是否打开、背包是否已满")
        subtitle.setStyleSheet("color:#64748b;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        controls = QGroupBox("游戏窗口")
        controls_layout = QHBoxLayout(controls)
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(430)
        self.refresh_button = QPushButton("重新检测")
        self.start_button = QPushButton("开始监控")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.refresh_button.clicked.connect(self.refresh_windows)
        self.start_button.clicked.connect(self.start_monitoring)
        self.stop_button.clicked.connect(self.stop_monitoring)
        controls_layout.addWidget(self.window_combo, 1)
        controls_layout.addWidget(self.refresh_button)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.stop_button)
        layout.addWidget(controls)

        content = QGridLayout()
        self.preview = QLabel("正在查找梦幻西游窗口…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(520, 390)
        self.preview.setStyleSheet("background:#111827;color:#cbd5e1;border-radius:8px;")
        content.addWidget(self.preview, 0, 0)

        right = QVBoxLayout()
        status_box = QGroupBox("背包状态")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("等待监控")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(90)
        self.status_label.setStyleSheet(
            "background:#f1f5f9;color:#475569;border-radius:8px;font-size:19px;font-weight:800;"
        )
        self.detail_label = QLabel("空格：-\n已占用：-\n置信度：-")
        self.detail_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.detail_label)
        right.addWidget(status_box)

        run_box = QGroupBox("运行状态")
        run_layout = QVBoxLayout(run_box)
        self.run_label = QLabel("未启动")
        self.metrics_label = QLabel("采集 0 | 分析 0")
        self.run_label.setWordWrap(True)
        self.metrics_label.setWordWrap(True)
        run_layout.addWidget(self.run_label)
        run_layout.addWidget(self.metrics_label)
        right.addWidget(run_box)
        right.addStretch(1)
        content.addLayout(right, 0, 1)
        layout.addLayout(content)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setFixedHeight(82)
        self.log.setPlaceholderText("提醒记录会显示在这里")
        layout.addWidget(self.log)

    def _selected_hwnd(self) -> int | None:
        value = self.window_combo.currentData()
        return int(value) if value is not None else None

    @Slot()
    def refresh_windows(self) -> None:
        if self.worker is not None:
            return
        current = self._selected_hwnd()
        try:
            keyword = self.config.capture.window_title_keyword.casefold()
            windows = [item for item in list_capturable_windows() if keyword in item.title.casefold()]
        except Exception as exc:  # noqa: BLE001 - native enumeration errors belong in the status area
            self.run_label.setText(f"窗口检测失败：{exc}")
            return
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        self._window_titles = {window.hwnd: window.title for window in windows}
        for window in windows:
            suffix = "（已最小化）" if window.minimized else ""
            self.window_combo.addItem(f"{window.title}  [0x{window.hwnd:X}]{suffix}", window.hwnd)
        if current in self._window_titles:
            self.window_combo.setCurrentIndex(list(self._window_titles).index(current))
        self.window_combo.blockSignals(False)
        found = bool(windows)
        self.start_button.setEnabled(found)
        if not found:
            self.run_label.setText("未检测到梦幻西游窗口")
            self.preview.setText("请先启动梦幻西游")
            self._auto_started_hwnd = None
            return
        self.run_label.setText(f"已检测到 {len(windows)} 个梦幻西游窗口")
        hwnd = self._selected_hwnd()
        if hwnd is not None and self._auto_started_hwnd is None:
            self._auto_started_hwnd = hwnd
            QTimer.singleShot(100, self.start_monitoring)

    @Slot()
    def start_monitoring(self) -> None:
        if self.worker is not None:
            return
        hwnd = self._selected_hwnd()
        if hwnd is None or not is_window_available(hwnd):
            self.run_label.setText("目标窗口已经关闭，请重新检测")
            return
        self.thread = QThread(self)
        self.worker = InventoryMonitorWorker(hwnd, self.config)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.preview_ready.connect(self._on_preview)
        self.worker.analysis_ready.connect(self._on_analysis)
        self.worker.event_ready.connect(self._on_event)
        self.worker.stats_ready.connect(self._on_stats)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self._on_finished)
        self.thread.finished.connect(self._on_thread_finished)
        self.window_combo.setEnabled(False)
        self.start_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.run_label.setText(f"正在监控：{self._window_titles.get(hwnd, hex(hwnd))}")
        self.log.appendPlainText(self.run_label.text())
        self._window_minimized = False
        self._poll_active_window_state()
        self.thread.start()

    @Slot()
    def stop_monitoring(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.run_label.setText("正在停止…")

    @Slot(object)
    def _on_preview(self, image: Any) -> None:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        qimage = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimage).scaled(
            self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(pixmap)

    @Slot(object)
    def _on_analysis(self, signal: dict[str, Any]) -> None:
        if self._window_minimized:
            return
        status = str(signal.get("status", "invalid"))
        text, foreground, background = self.STATUS_PRESENTATION.get(status, ("未知状态", "#475569", "#f1f5f9"))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"background:{background};color:{foreground};border-radius:8px;font-size:19px;font-weight:800;"
        )
        self.detail_label.setText(
            f"空格：{signal.get('empty_count', 0)}\n"
            f"已占用：{signal.get('occupied_count', 0)}\n"
            f"置信度：{float(signal.get('confidence', 0.0)):.0%}"
        )

    @Slot(object)
    def _on_event(self, event: Event) -> None:
        if self._window_minimized:
            return
        self.log.appendPlainText(f"{event.display_time}  {event.display_name}")
        messages = {
            "inventory_full": (QMessageBox.Icon.Critical, "背包已满", "检测到背包 20 个格子全部占用，请及时处理。"),
            "inventory_not_open": (
                QMessageBox.Icon.Warning,
                "请打开背包",
                "当前没有打开背包，程序无法判断是否已满。请让操作员工打开背包。",
            ),
            "inventory_detection_blocked": (
                QMessageBox.Icon.Warning,
                "背包检测被遮挡",
                "请关闭聊天、属性或其他遮挡窗口，并保持背包完整可见。",
            ),
        }
        details = messages.get(event.type)
        if details is None:
            return
        icon, title, message = details
        self._show_alert(icon, title, message, self.window_combo.currentText())

    def _show_alert(
        self,
        icon: QMessageBox.Icon,
        title: str,
        message: str,
        details: str = "",
    ) -> None:
        QApplication.beep()
        alert = QMessageBox(self)
        alert.setIcon(icon)
        alert.setWindowTitle(title)
        alert.setText(message)
        if details:
            alert.setInformativeText(details)
        alert.setWindowModality(Qt.WindowModality.NonModal)
        alert.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        alert.finished.connect(lambda _result, current=alert: self.alerts.discard(current))
        self.alerts.add(alert)
        alert.show()
        alert.raise_()

    @Slot()
    def _poll_active_window_state(self) -> None:
        if self.worker is None:
            self._window_minimized = False
            return
        hwnd = self.worker.hwnd
        if not is_window_available(hwnd):
            return
        minimized = is_window_minimized(hwnd)
        if minimized == self._window_minimized:
            return
        self._window_minimized = minimized
        title = self._window_titles.get(hwnd, hex(hwnd))
        if minimized:
            self.status_label.setText("游戏窗口已最小化\n检测已暂停，请恢复窗口")
            self.status_label.setStyleSheet(
                "background:#fef3c7;color:#b45309;border-radius:8px;font-size:19px;font-weight:800;"
            )
            self.detail_label.setText("当前结果不再更新，恢复窗口后会自动继续检测。")
            self.run_label.setText(f"检测已暂停：{title}")
            self.log.appendPlainText(f"游戏窗口已最小化，检测暂停：{title}")
            self._show_alert(
                QMessageBox.Icon.Warning,
                "背包检测已暂停",
                "梦幻西游窗口已最小化，请恢复窗口后继续检测。",
                title,
            )
        else:
            self.status_label.setText("窗口已恢复，正在重新检测…")
            self.status_label.setStyleSheet(
                "background:#e0f2fe;color:#0369a1;border-radius:8px;font-size:19px;font-weight:800;"
            )
            self.detail_label.setText("等待新的游戏画面…")
            self.run_label.setText(f"正在监控：{title}")
            self.log.appendPlainText(f"游戏窗口已恢复，检测继续：{title}")

    @Slot(object)
    def _on_stats(self, stats: dict[str, Any]) -> None:
        self.metrics_label.setText(
            f"采集 {stats['captured']} | 分析 {stats['analyzed']}\n"
            f"采集 {stats['capture_fps']:.1f} FPS | 分析 {stats['analysis_fps']:.1f} FPS"
        )

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        summary = message.strip().splitlines()[0] if message.strip() else "未知错误"
        self.log.appendPlainText(f"监控失败：{summary}")
        QMessageBox.critical(self, "背包监控失败", summary)

    @Slot()
    def _on_finished(self) -> None:
        self.run_label.setText("监控已停止")

    @Slot()
    def _on_thread_finished(self) -> None:
        self._window_minimized = False
        self.worker = None
        self.thread = None
        self.window_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(self.window_combo.count() > 0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.scan_timer.stop()
        self.window_state_timer.stop()
        for alert in tuple(self.alerts):
            alert.close()
        if self.worker is not None:
            self.worker.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


def main(argv: list[str] | None = None) -> int:
    from .multi_inventory_app import main as multi_window_main

    return multi_window_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

"""Minimal Windows desktop application dedicated to inventory monitoring."""

from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from threading import Event as ThreadEvent
from time import monotonic
from typing import Any, ClassVar

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

import cv2

from .capture import make_source
from .config import AppConfig, load_config
from .event_machine import EventMachine
from .inventory_fullness import InventoryAnalysis, InventoryFullnessDetector
from .models import Event, Observation
from .paths import application_root, resolve_application_path
from .sampler import AdaptiveSampler, SamplingProfile
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

    def set_paused(self, paused: bool) -> None:
        """Pause analysis without tearing down the window capture session."""

        if paused:
            self._pause_requested.set()
        else:
            self._pause_requested.clear()

    def stop(self) -> None:
        self._stop_requested.set()
        if self._source is not None:
            self._source.close()

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
                if self._pause_requested.is_set():
                    was_paused = True
                    continue
                if was_paused:
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
                    continue
                now = monotonic()
                if now - last_preview >= self.PREVIEW_INTERVAL_SECONDS:
                    last_preview = now
                    preview = self._preview(packet.image)
                    self.preview_ready.emit(preview)
                    self.preview_for_window.emit(self.hwnd, preview)
                if sampler.accept(packet):
                    analyzed += 1
                    analysis = detector.analyze(packet.image)
                    signal = _analysis_signal(analysis)
                    self.analysis_ready.emit(signal)
                    self.analysis_for_window.emit(self.hwnd, signal)
                    observation = Observation(
                        packet.frame_index,
                        packet.timestamp_ms,
                        {"inventory_fullness": signal},
                        analysis.confidence,
                        "inventory-monitor",
                    )
                    for event in machine.update(observation):
                        account = parse_account_info(self.window_title)
                        event.payload.setdefault("window_hwnd", self.hwnd)
                        event.payload.setdefault("window_title", self.window_title)
                        event.payload.setdefault("secondary_region", account.secondary_region)
                        event.payload.setdefault("role_name", account.role_name)
                        event.payload.setdefault("role_id", account.role_id)
                        sink.write(event)
                        self.event_ready.emit(event)
                        self.event_for_window.emit(self.hwnd, event)
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

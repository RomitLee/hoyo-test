"""Minimal Windows desktop UI for OBS Virtual Camera real-time analysis."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2

from .capture import list_video_devices, make_source
from .cli import _make_perception, _make_sink
from .config import AppConfig, load_config
from .event_machine import EventMachine
from .evidence import EvidenceWriter
from .models import Event
from .obs import is_obs_running
from .realtime import LiveStats, RealtimeAnalyzer

try:
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("桌面界面需要 PySide6，请先执行：uv sync --dev") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RealtimeWorker(QObject):
    """Run the existing real-time analyzer outside the Qt UI thread."""

    frame_ready = Signal(object)
    event_ready = Signal(object)
    started = Signal()
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, source_name: str, device_index: int, config: AppConfig) -> None:
        super().__init__()
        self.source_name = source_name
        self.device_index = device_index
        self.config = config
        self.analyzer: RealtimeAnalyzer | None = None

    def _on_frame(self, packet: Any) -> None:
        # Copy before crossing the thread boundary: OpenCV may reuse its buffer.
        self.frame_ready.emit(packet.image.copy())

    def _on_event(self, event: Event) -> None:
        self.event_ready.emit(event)

    @Slot()
    def run(self) -> None:
        source = None
        try:
            source = make_source(
                self.source_name,
                self.config.capture.path,
                self.device_index,
                self.source_name,
            )
            self.analyzer = RealtimeAnalyzer(
                source,
                self.config,
                _make_perception(self.config),
                EventMachine(),
                _make_sink(self.config),
                EvidenceWriter(
                    self.config.evidence.directory,
                    self.config.evidence.pre_buffer_ms,
                ),
                on_frame=self._on_frame,
                on_event=self._on_event,
            )
            self.started.emit()
            self.finished.emit(self.analyzer.run())
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed.emit(str(exc))
        finally:
            if source is not None:
                source.close()

    def stop(self) -> None:
        if self.analyzer is not None:
            self.analyzer.request_stop()


class MainWindow(QMainWindow):
    """Small status dashboard for the OBS-to-analysis MVP."""

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Hoyo 游戏视频实时分析")
        self.resize(1100, 720)

        self.config_path = config_path or PROJECT_ROOT / "configs" / "default.toml"
        self.config = load_config(self.config_path if self.config_path.exists() else None)
        self.thread: QThread | None = None
        self.worker: RealtimeWorker | None = None
        self.last_pixmap: QPixmap | None = None
        self.last_captured = 0
        self.last_output_path: Path | None = None
        self.running = False

        self._build_ui()
        self._refresh_devices()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(1000)
        self._refresh_status()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        controls = QGroupBox("实时输入")
        form = QFormLayout(controls)
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem("OBS Virtual Camera", "obs-virtual-camera")
        self.source_combo.addItem("HDMI 采集卡", "capture-card")
        self.source_combo.addItem("普通摄像头", "camera")
        self.device_spin = QSpinBox()
        self.device_spin.setRange(0, 99)
        self.device_spin.setValue(0)
        self.refresh_button = QPushButton("刷新设备")
        self.test_button = QPushButton("测试设备")
        source_row.addWidget(self.source_combo, 1)
        source_row.addWidget(QLabel("设备号"))
        source_row.addWidget(self.device_spin)
        source_row.addWidget(self.refresh_button)
        source_row.addWidget(self.test_button)
        form.addRow("视频源", source_row)

        self.start_button = QPushButton("开始实时分析")
        self.stop_button = QPushButton("停止分析")
        self.stop_button.setEnabled(False)
        self.refresh_button.clicked.connect(self._refresh_devices)
        self.test_button.clicked.connect(self._test_device)
        self.source_combo.currentIndexChanged.connect(self._update_start_availability)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        form.addRow(button_row)
        layout.addWidget(controls)

        grid = QGridLayout()
        self.preview = QLabel("等待视频画面…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(640, 360)
        self.preview.setStyleSheet("background:#101010;color:#bbbbbb;")
        grid.addWidget(self.preview, 0, 0, 1, 2)

        status = QGroupBox("运行状态")
        status_layout = QFormLayout(status)
        self.obs_label = QLabel("检测中")
        self.capture_label = QLabel("未启动")
        self.output_label = QLabel("未启动")
        self.metrics_label = QLabel("采集 0 | 分析 0 | 丢弃 0")
        status_layout.addRow("OBS 进程", self.obs_label)
        status_layout.addRow("视频采集", self.capture_label)
        status_layout.addRow("事件输出", self.output_label)
        status_layout.addRow("指标", self.metrics_label)
        grid.addWidget(status, 1, 0)

        self.events = QTableWidget(0, 4)
        self.events.setHorizontalHeaderLabels(["时间", "事件", "置信度", "数据"])
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        grid.addWidget(self.events, 1, 1)
        layout.addLayout(grid, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        layout.addWidget(self.log)

    def _refresh_devices(self) -> None:
        try:
            devices = list_video_devices(10)
        except RuntimeError as exc:
            self.log.appendPlainText(str(exc))
            return
        message = ", ".join(map(str, devices)) if devices else "无"
        self.log.appendPlainText(f"可用 OpenCV 视频设备：{message}")

    def _test_device(self) -> None:
        source = None
        try:
            source = make_source(
                self.source_combo.currentData(),
                self.config.capture.path,
                self.device_spin.value(),
                "ui-test",
            )
            next(iter(source))
            self.capture_label.setText("设备可读取")
            self.log.appendPlainText("设备测试成功，已读取到一帧")
        except (OSError, RuntimeError, StopIteration, ValueError) as exc:
            self.capture_label.setText("设备不可用")
            QMessageBox.warning(self, "设备测试失败", str(exc))
        finally:
            if source is not None:
                source.close()

    def _requires_obs(self) -> bool:
        return self.source_combo.currentData() == "obs-virtual-camera"

    def _update_start_availability(self, *_args: Any) -> None:
        """Do not offer an OBS-camera start while OBS itself is absent."""
        if self.running:
            return
        obs_missing = self._requires_obs() and not is_obs_running()
        self.start_button.setEnabled(not obs_missing)
        if obs_missing:
            self.start_button.setToolTip("请先启动 OBS Studio，再开始实时分析")
            self.capture_label.setText("等待 OBS")
        else:
            self.start_button.setToolTip("")
            if self.capture_label.text() == "等待 OBS":
                self.capture_label.setText("未启动")

    def _start(self) -> None:
        if self.running:
            return
        if self._requires_obs() and not is_obs_running():
            self.capture_label.setText("等待 OBS")
            self.log.appendPlainText("未检测到 OBS，已阻止启动 OBS Virtual Camera 分析。")
            QMessageBox.warning(self, "无法开始分析", "当前未检测到 OBS Studio。请先启动 OBS，并点击“启动虚拟摄像机”。")
            self._update_start_availability()
            return

        self.last_captured = 0
        self.last_output_path = Path(self.config.output.event_log)
        self.events.setRowCount(0)
        self.log.appendPlainText("正在启动实时分析…")
        self.thread = QThread(self)
        self.worker = RealtimeWorker(
            self.source_combo.currentData(),
            self.device_spin.value(),
            self.config,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.started.connect(self._on_started)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.event_ready.connect(self._on_event)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._on_thread_finished)
        self.running = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.thread.start()

    def _stop(self) -> None:
        if self.worker is not None:
            self.log.appendPlainText("正在停止…")
            self.worker.stop()

    def _on_started(self) -> None:
        self.capture_label.setText("已启动，等待帧")
        self.output_label.setText("输出通道已开启")

    def _on_frame(self, image: Any) -> None:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image_qt = QImage(
            rgb.data,
            width,
            height,
            channels * width,
            QImage.Format.Format_RGB888,
        ).copy()
        self.last_pixmap = QPixmap.fromImage(image_qt)
        self._render_preview()

    def _render_preview(self) -> None:
        if self.last_pixmap is not None:
            self.preview.setPixmap(
                self.last_pixmap.scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._render_preview()

    def _on_event(self, event: Event) -> None:
        row = self.events.rowCount()
        self.events.insertRow(row)
        values = [
            event.timestamp,
            event.type,
            f"{event.confidence:.2f}",
            str(event.payload),
        ]
        for column, value in enumerate(values):
            self.events.setItem(row, column, QTableWidgetItem(value))
        self.events.scrollToBottom()
        self.log.appendPlainText(f"{event.timestamp} {event.type} {event.payload}")
        self.output_label.setText("正在输出事件")

    def _refresh_status(self) -> None:
        obs_running = is_obs_running()
        self.obs_label.setText("运行中" if obs_running else "未检测到")
        self._update_start_availability()
        if self.worker is None or self.worker.analyzer is None or not self.running:
            return

        stats = self.worker.analyzer.stats
        self.metrics_label.setText(
            f"采集 {stats.captured_frames} | 分析 {stats.analyzed_frames} | "
            f"丢弃 {stats.dropped_frames} | 分析 {stats.analysis_fps:.1f} FPS"
        )
        if stats.captured_frames > self.last_captured:
            self.capture_label.setText("正常采集")
            self.last_captured = stats.captured_frames
        else:
            self.capture_label.setText("等待新帧")
        if self.last_output_path is not None and self.last_output_path.exists():
            self.output_label.setText("输出文件已打开")

    def _on_finished(self, stats: LiveStats) -> None:
        self.log.appendPlainText(
            f"实时分析结束：采集 {stats.captured_frames}，分析 {stats.analyzed_frames}，丢弃 {stats.dropped_frames}"
        )

    def _on_failed(self, message: str) -> None:
        self.log.appendPlainText(f"实时分析失败：{message}")
        QMessageBox.critical(self, "实时分析失败", message)

    def _on_thread_finished(self) -> None:
        self.running = False
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.capture_label.setText("已停止")
        self.output_label.setText("已关闭")
        self.worker = None
        self.thread = None

    def closeEvent(self, event: Any) -> None:
        if self.worker is not None:
            self.worker.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

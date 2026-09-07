"""Windows desktop UI for direct game-window capture and real-time analysis."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

# Silence expected native OpenCV probe messages such as "Camera index out of range".
os.environ.setdefault("OPENCV_LOG_LEVEL", os.environ.get("HOYO_OPENCV_LOG_LEVEL", "SILENT"))

import cv2

from .capture import make_source
from .cli import _make_perception, _make_sink
from .config import AppConfig, load_config
from .event_machine import EventMachine
from .evidence import EvidenceWriter
from .models import Event
from .realtime import LiveStats, RealtimeAnalyzer
from .wgc import (
    WindowInfo,
    WindowOcclusionStatus,
    get_window_title,
    is_window_available,
    is_window_minimized,
    list_capturable_windows,
    measure_window_occlusion,
)

try:
    from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSizePolicy,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("桌面界面需要 PySide6，请先执行：uv sync --dev") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WGC_SOURCE = "windows-graphics-capture"
OCCLUSION_WARNING_RATIO = 0.01
WINDOW_WIDTH = 880
WINDOW_HEIGHT = 600


class RealtimeWorker(QObject):
    """Run the existing real-time analyzer outside the Qt UI thread.

    Preview frames use a bounded mailbox instead of putting every full-size
    NumPy image into Qt's queued-signal event queue. This keeps the preview
    responsive without allowing UI backpressure to exhaust memory.
    """

    PREVIEW_INTERVAL_SECONDS = 0.1  # 10 FPS is sufficient for a 300px preview.
    MAX_PREVIEW_EDGE = 600

    frame_ready = Signal()
    event_ready = Signal(object)
    started = Signal()
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, source_name: str, device_index: int, window_hwnd: int | None, config: AppConfig) -> None:
        super().__init__()
        self.source_name = source_name
        self.device_index = device_index
        self.window_hwnd = window_hwnd
        self.config = config
        self.analyzer: RealtimeAnalyzer | None = None
        self._preview_lock = Lock()
        self._latest_preview: Any | None = None
        self._preview_signal_pending = False
        self._last_preview_emit_at = 0.0

    def _prepare_preview(self, image: Any) -> Any:
        """Make one bounded-size preview image, never a full-size signal payload."""
        height, width = image.shape[:2]
        longest_edge = max(height, width)
        if longest_edge <= self.MAX_PREVIEW_EDGE:
            return image.copy()
        scale = self.MAX_PREVIEW_EDGE / longest_edge
        target_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        return cv2.resize(image, target_size, interpolation=cv2.INTER_AREA)

    def take_latest_preview(self) -> Any | None:
        """Take the newest pending preview and release the queued-signal gate."""
        with self._preview_lock:
            image = self._latest_preview
            self._latest_preview = None
            self._preview_signal_pending = False
            return image

    def _on_frame(self, packet: Any) -> None:
        now = monotonic()
        if now - self._last_preview_emit_at < self.PREVIEW_INTERVAL_SECONDS:
            return
        self._last_preview_emit_at = now
        preview = self._prepare_preview(packet.image)

        should_signal = False
        with self._preview_lock:
            # Replace, rather than append: only the freshest preview matters.
            self._latest_preview = preview
            if not self._preview_signal_pending:
                self._preview_signal_pending = True
                should_signal = True
        if should_signal:
            self.frame_ready.emit()

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
                window_hwnd=self.window_hwnd,
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
        except Exception as exc:  # noqa: BLE001 - worker must report every pipeline failure to the UI
            details = traceback.format_exc()
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n详细堆栈：\n{details}")
        finally:
            if source is not None:
                source.close()

    def stop(self) -> None:
        if self.analyzer is not None:
            self.analyzer.request_stop()


class WindowScanThread(QThread):
    """Enumerate capturable top-level windows outside the Qt UI thread."""

    windows_ready = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.windows_ready.emit(list_capturable_windows())
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed.emit(str(exc))


class OcclusionCheckThread(QThread):
    """Measure target-window overlap without blocking the Qt UI thread."""

    status_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, window_hwnd: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window_hwnd = window_hwnd

    def run(self) -> None:
        try:
            self.status_ready.emit(measure_window_occlusion(self.window_hwnd))
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed.emit(str(exc))


class SquarePreview(QLabel):
    """Small 1:1 preview canvas that letterboxes the game's native 4:3 frame."""

    DEFAULT_SIZE = 300
    MAX_SIZE = 300

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 180)
        self.setMaximumSize(self.MAX_SIZE, self.MAX_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(self.MAX_SIZE, self.MAX_SIZE)
        self.setStyleSheet("background:#101010;color:#bbbbbb;border:1px solid #3f3f46;")

    def sizeHint(self) -> QSize:
        return QSize(self.DEFAULT_SIZE, self.DEFAULT_SIZE)


class MainWindow(QMainWindow):
    """Status dashboard with WGC game-window capture as the default input."""

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("梦幻子霖AI分析工具")
        # 面向边玩游戏边查看结果的固定桌面布局，避免用户误拖拽导致比例变化。
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)

        self.config_path = config_path or PROJECT_ROOT / "configs" / "default.toml"
        self.config = load_config(self.config_path if self.config_path.exists() else None)
        self.thread: QThread | None = None
        self.worker: RealtimeWorker | None = None
        self.last_pixmap: QPixmap | None = None
        self.last_captured = 0
        self.last_output_path: Path | None = None
        self.running = False
        self.window_scan_thread: WindowScanThread | None = None
        self.occlusion_check_thread: OcclusionCheckThread | None = None
        self.selected_window_hwnd: int | None = None
        self.selected_window_name = "未检测到梦幻西游窗口"
        self.current_occluded = False
        self.last_occlusion_warning_hwnd: int | None = None
        self.auto_start_enabled = True
        self.auto_start_in_progress = False
        self.initial_window_scan_completed = False
        self.game_window_present = False
        self.missing_game_scan_ticks = 0
        self.window_scan_quiet = False

        self._build_ui()
        self._refresh_windows()
        self._on_target_changed()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(1000)
        self._refresh_status()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        root.setStyleSheet(
            """
            QWidget#appRoot { background: #f4f7fb; color: #26324a; }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #e4eaf3;
                border-radius: 12px;
                margin-top: 10px;
                padding: 10px 10px 8px 10px;
                font-weight: 700;
                color: #33415c;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #6574cd;
            }
            QLabel#brandTitle { color: #4353b8; font-size: 18px; font-weight: 800; }
            QLabel#brandSubtitle { color: #7c8aa5; font-size: 11px; }
            QLabel#targetValue {
                background: #eef2ff;
                border: 1px solid #d9ddff;
                border-radius: 8px;
                padding: 5px 10px;
                color: #4c51a3;
                font-weight: 700;
            }
            QLabel#avatarLabel {
                background: #e9edff;
                border: 1px solid #d5dcff;
                border-radius: 14px;
                color: #5865c4;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#loginState { color: #66738c; font-weight: 700; }
            QLabel#captureStateTitle { color: #7c8aa5; font-size: 11px; font-weight: 700; }
            QLabel.capturePill {
                background: #f1f4f8;
                border: 1px solid #e4eaf1;
                border-radius: 8px;
                color: #9aa5b8;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 700;
            }
            QWidget#captureStatusCard, QWidget#connectionCard {
                background: #ffffff;
                border: 1px solid #e4eaf3;
                border-radius: 12px;
            }
            QPushButton {
                min-height: 30px;
                padding: 4px 14px;
                border: 0;
                border-radius: 9px;
                font-weight: 700;
            }
            QPushButton#startButton { background: #6c63e8; color: white; }
            QPushButton#startButton:hover { background: #584fd0; }
            QPushButton#startButton:disabled { background: #c8cce0; color: #ffffff; }
            QPushButton#pauseButton { background: #ffd166; color: #6b4e00; }
            QPushButton#pauseButton:hover { background: #f4bd3e; }
            QPushButton#pauseButton:disabled { background: #e7eaf0; color: #9aa5b8; }
            QPushButton#loginButton {
                min-height: 24px; max-height: 24px; min-width: 0px; max-width: 44px;
                padding: 0px 2px; border-radius: 8px;
                background: #6c63e8; color: white;
            }
            QPushButton#loginButton:hover { background: #584fd0; }
            QWidget#navPanel {
                background: #ffffff; border: 1px solid #e4eaf3; border-radius: 12px;
            }
            QLabel#navTitle { color: #9aa5b8; font-size: 10px; font-weight: 800; }
            QPushButton#navButton {
                min-height: 38px; max-height: 38px; padding: 4px 8px;
                text-align: left; border-radius: 9px; color: #66738c;
                background: transparent; font-weight: 700;
            }
            QPushButton#navButton:hover { background: #f0f3ff; color: #4c51a3; }
            QPushButton#navButton:checked { background: #e9edff; color: #4c51a3; }
            QTableWidget, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #e4eaf3;
                border-radius: 12px;
                gridline-color: #edf0f6;
            }
            QHeaderView::section {
                background: #f0f3ff;
                color: #5965af;
                border: 0;
                padding: 6px;
                font-weight: 700;
            }
            """
        )
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)
        self.setCentralWidget(root)

        # 顶部是公共账号区域；采集状态属于 AI 分析页面，不在这里占用空间。
        header = QWidget()
        header.setFixedHeight(50)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(12)

        account = QHBoxLayout()
        account.setContentsMargins(0, 0, 0, 0)
        account.setSpacing(6)
        self.avatar_label = QLabel("未")
        self.avatar_label.setObjectName("avatarLabel")
        self.avatar_label.setFixedSize(28, 28)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        account.addWidget(self.avatar_label)
        account_info = QVBoxLayout()
        account_info.setContentsMargins(0, 0, 0, 0)
        account_info.setSpacing(0)
        self.login_state_label = QLabel("未登录")
        self.login_state_label.setObjectName("loginState")
        self.login_hint_label = QLabel("登录后与好友分享游戏记录")
        self.login_hint_label.setStyleSheet("color:#9aa5b8;font-size:10px;")
        account_info.addWidget(self.login_state_label)
        account_info.addWidget(self.login_hint_label)
        account.addLayout(account_info)
        self.login_button = QPushButton("登录")
        self.login_button.setObjectName("loginButton")
        self.login_button.setFixedWidth(44)
        self.login_button.setFixedHeight(24)
        self.login_button.setMinimumWidth(44)
        self.login_button.setMaximumWidth(44)
        self.login_button.clicked.connect(self._on_login_clicked)
        account.addWidget(self.login_button)
        header_layout.addLayout(account, 1)

        brand = QVBoxLayout()
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(0)
        brand_title = QLabel("梦幻子霖AI分析工具")
        brand_title.setObjectName("brandTitle")
        brand_subtitle = QLabel("边玩边记录 · 和好友一起分享每个精彩瞬间 ✨")
        brand_subtitle.setObjectName("brandSubtitle")
        brand.addWidget(brand_title, 0, Qt.AlignmentFlag.AlignRight)
        brand.addWidget(brand_subtitle, 0, Qt.AlignmentFlag.AlignRight)
        header_layout.addLayout(brand)
        layout.addWidget(header)

        # 左侧导航是社交产品的公共入口；当前只有 AI 分析页面有实际内容。
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        self.nav_panel = QWidget()
        self.nav_panel.setObjectName("navPanel")
        self.nav_panel.setFixedWidth(112)
        nav_layout = QVBoxLayout(self.nav_panel)
        nav_layout.setContentsMargins(8, 12, 8, 10)
        nav_layout.setSpacing(5)
        nav_title = QLabel("梦幻社区")
        nav_title.setObjectName("navTitle")
        nav_layout.addWidget(nav_title)
        self.nav_buttons: dict[str, QPushButton] = {}
        for page_name in ("AI分析", "聊天室", "装备鉴赏", "子霖商行", "个人中心"):
            button = QPushButton(page_name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, name=page_name: self._switch_page(name))
            self.nav_buttons[page_name] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        body.addWidget(self.nav_panel)

        self.page_container = QWidget()
        page_layout = QVBoxLayout(self.page_container)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(6)

        self.ai_content = QWidget()
        ai_layout = QVBoxLayout(self.ai_content)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(0)

        # 该提示独立于“运行状态”卡片，专门承载自动检测/自动采集等较长信息。
        self.game_status_banner = QLabel("正在检测梦幻西游窗口…")
        self.game_status_banner.setWordWrap(True)
        self.game_status_banner.setMinimumHeight(32)
        self.game_status_banner.setMaximumHeight(56)
        self.game_status_banner.setStyleSheet(
            "background:#eaf0ff;color:#4c5ab5;border:1px solid #cdd7ff;padding:4px 8px;border-radius:8px;font-weight:600;"
        )

        self.controls = QWidget()
        self.controls.setObjectName("connectionCard")
        self.controls.setFixedHeight(48)
        connection_layout = QHBoxLayout(self.controls)
        connection_layout.setContentsMargins(8, 4, 8, 4)
        connection_layout.setSpacing(8)
        connection_layout.addWidget(QLabel("当前窗口"))
        self.window_text_label = QLabel("正在检测梦幻西游窗口…")
        self.window_text_label.setObjectName("targetValue")
        self.window_text_label.setToolTip("程序只会自动采集标题包含“梦幻西游”的窗口")
        connection_layout.addWidget(self.window_text_label, 1)

        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("startButton")
        self.start_button.setToolTip("开始实时采集和分析")
        self.stop_button = QPushButton("暂停")
        self.stop_button.setObjectName("pauseButton")
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("暂停当前实时采集和分析")
        connection_layout.addWidget(self.start_button)
        connection_layout.addWidget(self.stop_button)

        # 采集状态紧跟在“当前窗口 + 开始/暂停”下方，避免被公共登录区挤压。
        self.capture_status_card = QWidget()
        self.capture_status_card.setObjectName("captureStatusCard")
        self.capture_status_card.setFixedHeight(38)
        capture_status_layout = QHBoxLayout(self.capture_status_card)
        capture_status_layout.setContentsMargins(8, 3, 8, 3)
        capture_status_layout.setSpacing(5)
        capture_title = QLabel("采集状态")
        capture_title.setObjectName("captureStateTitle")
        capture_status_layout.addWidget(capture_title)
        self.captured_state_label = QLabel("● 采集中")
        self.captured_state_label.setProperty("class", "capturePill")
        self.captured_state_label.setObjectName("capturedState")
        self.occluded_state_label = QLabel("● 窗口被遮挡")
        self.occluded_state_label.setProperty("class", "capturePill")
        self.occluded_state_label.setObjectName("occludedState")
        self.not_captured_state_label = QLabel("● 未采集")
        self.not_captured_state_label.setProperty("class", "capturePill")
        self.not_captured_state_label.setObjectName("notCapturedState")
        capture_status_layout.addWidget(self.captured_state_label)
        capture_status_layout.addWidget(self.occluded_state_label)
        capture_status_layout.addWidget(self.not_captured_state_label)
        capture_status_layout.addStretch(1)

        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)

        # AI 分析页面：左侧预览/运行状态，右侧采集控制/连接提示/事件结果。
        content_grid = QGridLayout()
        content_grid.setContentsMargins(0, 0, 0, 0)
        content_grid.setHorizontalSpacing(10)
        content_grid.setColumnMinimumWidth(0, SquarePreview.MAX_SIZE)
        content_grid.setColumnStretch(0, 0)
        content_grid.setColumnStretch(1, 1)
        content_grid.setRowStretch(0, 1)

        left_panel = QWidget()
        left_panel.setFixedWidth(SquarePreview.MAX_SIZE)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.preview = SquarePreview()
        self.preview.setText("请选择梦幻西游窗口并开始分析…")
        self.preview.setToolTip("1:1 预览幕布；游戏原始画面按 4:3 比例自适应显示")
        left_layout.addWidget(self.preview)

        status = QGroupBox("运行状态")
        status_layout = QFormLayout(status)
        status_layout.setContentsMargins(8, 6, 8, 6)
        status_layout.setVerticalSpacing(3)
        self.target_label = QLabel("未选择")
        self.capture_label = QLabel("未启动")
        self.occlusion_label = QLabel("等待选择窗口")
        self.output_label = QLabel("未启动")
        self.metrics_label = QLabel("采集 0 | 分析 0 | 丢弃 0")
        for label in (
            self.target_label,
            self.capture_label,
            self.occlusion_label,
            self.output_label,
            self.metrics_label,
        ):
            label.setWordWrap(True)
        status_layout.addRow("目标窗口", self.target_label)
        status_layout.addRow("窗口遮挡", self.occlusion_label)
        status_layout.addRow("视频采集", self.capture_label)
        status_layout.addRow("事件输出", self.output_label)
        status_layout.addRow("指标", self.metrics_label)
        status.setMinimumHeight(116)
        left_layout.addWidget(status, 1)
        self.runtime_status = status

        self.events = QTableWidget(0, 4)
        self.events.setHorizontalHeaderLabels(["时间", "事件", "置信度", "数据"])
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self.controls)
        right_layout.addWidget(self.capture_status_card)
        right_layout.addWidget(self.game_status_banner)
        right_layout.addWidget(self.events, 1)

        content_grid.addWidget(left_panel, 0, 0)
        content_grid.addWidget(right_panel, 0, 1)
        ai_layout.addLayout(content_grid)
        page_layout.addWidget(self.ai_content)

        self.page_status_label = QLabel("")
        self.page_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_status_label.setStyleSheet(
            "background:#ffffff;color:#7c8aa5;border:1px solid #e4eaf3;border-radius:12px;font-size:16px;font-weight:700;"
        )
        self.page_status_label.hide()
        page_layout.addWidget(self.page_status_label, 1)

        body.addWidget(self.page_container, 1)
        layout.addLayout(body, 1)
        self._set_capture_state("not_captured")
        self._switch_page("AI分析")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setFixedHeight(28)
        self.log.setPlaceholderText("运行动态会显示在这里…")
        layout.addWidget(self.log)

    def _set_capture_state(self, state: str) -> None:
        """Highlight one of the three player-facing capture states."""
        inactive = (
            "background:#f1f4f8;color:#9aa5b8;border:1px solid #e4eaf1;"
            "border-radius:8px;padding:3px 8px;font-size:11px;font-weight:700;"
        )
        active_styles = {
            "captured": "background:#e8f8ef;color:#27724b;border:1px solid #b9e8ca;border-radius:8px;padding:3px 8px;font-size:11px;font-weight:700;",
            "occluded": "background:#fff0f0;color:#c24141;border:1px solid #ffcaca;border-radius:8px;padding:3px 8px;font-size:11px;font-weight:700;",
            "not_captured": "background:#fff7e6;color:#9a6700;border:1px solid #f3d28b;border-radius:8px;padding:3px 8px;font-size:11px;font-weight:700;",
        }
        labels = {
            "captured": self.captured_state_label,
            "occluded": self.occluded_state_label,
            "not_captured": self.not_captured_state_label,
        }
        for name, label in labels.items():
            label.setStyleSheet(active_styles[name] if name == state else inactive)

    @Slot(str)
    def _switch_page(self, page_name: str) -> None:
        """切换左侧导航页面；未实现的社交模块先显示友好的占位页。"""
        for name, nav_button in self.nav_buttons.items():
            nav_button.setChecked(name == page_name)

        if page_name == "AI分析":
            self.ai_content.show()
            self.page_status_label.hide()
            return

        self.ai_content.hide()
        self.page_status_label.setText(f"{page_name}功能即将上线")
        self.page_status_label.show()

    @Slot()
    def _on_login_clicked(self) -> None:
        """Keep the account entry point visible until the account service is connected."""
        QMessageBox.information(
            self,
            "登录梦幻子霖",
            "登录功能即将上线。登录后可保存个人记录，并与好友分享游戏事件。",
        )

    def set_logged_in_user(self, nickname: str, avatar_path: Path | str | None = None) -> None:
        """Update the account card for a future authenticated user session."""
        nickname = nickname.strip() or "梦幻玩家"
        self.login_state_label.setText(nickname)
        self.login_hint_label.setText("已登录 · 可分享游戏记录")
        self.login_button.hide()
        self.avatar_label.setText(nickname[:1])
        if avatar_path:
            pixmap = QPixmap(str(avatar_path))
            if not pixmap.isNull():
                self.avatar_label.setText("")
                self.avatar_label.setPixmap(
                    pixmap.scaled(
                        self.avatar_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def _current_source(self) -> str:
        return WGC_SOURCE

    def _current_window_hwnd(self) -> int | None:
        return self.selected_window_hwnd

    def _uses_window_capture(self) -> bool:
        return True

    def _on_target_changed(self) -> None:
        self.current_occluded = False
        self._set_capture_state("not_captured")
        self.last_occlusion_warning_hwnd = None
        self.occluded_state_label.setText("● 窗口被遮挡")
        self.occluded_state_label.setToolTip("")
        self.occlusion_label.setStyleSheet("")
        self.occlusion_label.setText("检测中" if self._current_window_hwnd() else "等待选择窗口")
        self._update_start_availability()
        self._refresh_occlusion_status_async()

    # 保留内部兼容入口，当前 UI 已不再显示来源或窗口下拉框。
    def _on_window_selection_changed(self, *_args: Any) -> None:
        self._on_target_changed()

    def _on_source_changed(self, *_args: Any) -> None:
        self._on_target_changed()

    def _game_window_keyword(self) -> str:
        return getattr(self.config.capture, "window_title_keyword", "梦幻西游").strip() or "梦幻西游"

    def _is_game_window_title(self, title: str) -> bool:
        return self._game_window_keyword().casefold() in title.casefold()

    def _refresh_windows(self, _checked: bool = False, *, quiet: bool = False) -> None:
        if self.window_scan_thread is not None and self.window_scan_thread.isRunning():
            return
        self.window_scan_quiet = quiet
        self.window_text_label.setText("正在检测梦幻西游窗口…")
        if not quiet:
            self.log.appendPlainText("正在检测梦幻西游窗口…")
        self.window_scan_thread = WindowScanThread(self)
        self.window_scan_thread.windows_ready.connect(self._on_windows_ready)
        self.window_scan_thread.failed.connect(self._on_window_scan_failed)
        self.window_scan_thread.finished.connect(self._on_window_scan_finished)
        self.window_scan_thread.finished.connect(self.window_scan_thread.deleteLater)
        self.window_scan_thread.start()

    @Slot(object)
    def _on_windows_ready(self, windows: list[WindowInfo]) -> None:
        previous_hwnd = self.selected_window_hwnd
        own_hwnd = int(self.winId())
        game_windows = [info for info in windows if info.hwnd != own_hwnd and self._is_game_window_title(info.title)]
        selected: WindowInfo | None = None
        if game_windows:
            selected = next((info for info in game_windows if info.hwnd == previous_hwnd), None)
            if selected is None:
                selected = next((info for info in game_windows if not info.minimized), game_windows[0])
            self.selected_window_hwnd = selected.hwnd
            self.selected_window_name = selected.display_name
        else:
            self.selected_window_hwnd = None
            self.selected_window_name = "未检测到梦幻西游窗口"
        self._on_target_changed()

        found = selected is not None
        if found:
            self.missing_game_scan_ticks = 0
            if not self.game_window_present or not self.window_scan_quiet:
                self.log.appendPlainText(f"已检测到 {len(game_windows)} 个梦幻西游窗口")
            self.game_window_present = True
            # 找到游戏窗口后不再显示“已检测到……已开始自动采集”的横幅，
            # 连接情况通过“当前窗口”和下方采集状态直接表达。
            self.game_status_banner.hide()
            if self.auto_start_enabled and not self.running:
                self.auto_start_enabled = False
                self.auto_start_in_progress = True
                QTimer.singleShot(0, self._start_automatically)
        else:
            if self.game_window_present or not self.initial_window_scan_completed:
                self.log.appendPlainText("未检测到梦幻西游窗口，暂不启动监控；程序将继续自动检测。")
            self.game_window_present = False
            self.game_status_banner.setText("⚠ 未检测到梦幻西游窗口，当前不能监控。请先启动游戏，程序会继续自动检测。")
            self.game_status_banner.setStyleSheet(
                "background:#fff7e6;color:#9a6700;border:1px solid #f3d28b;padding:6px 8px;border-radius:8px;font-weight:600;"
            )

        self.initial_window_scan_completed = True
        self._update_start_availability()

    @Slot(str)
    def _on_window_scan_failed(self, message: str) -> None:
        self.log.appendPlainText(f"刷新窗口失败：{message}")

    @Slot()
    def _on_window_scan_finished(self) -> None:
        self.window_scan_thread = None
        self.window_scan_quiet = False
        self._update_start_availability()

    def _validate_current_target(self, *, show_message: bool) -> bool:
        hwnd = self._current_window_hwnd()
        if hwnd is None:
            message = "未检测到梦幻西游窗口，请先启动游戏。"
        elif not is_window_available(hwnd):
            message = "梦幻西游窗口已经关闭，程序会自动重新检测。"
        elif not self._is_game_window_title(get_window_title(hwnd)):
            message = "当前窗口不是梦幻西游窗口，程序拒绝开始采集。"
        else:
            # Minimized windows are allowed. WGC keeps the session alive and
            # resumes automatically if the game pauses rendering while minimized.
            return True
        if show_message:
            QMessageBox.warning(self, "无法开始采集", message)
        return False

    def _update_start_availability(self, *_args: Any) -> None:
        if self.running:
            return
        scanning_windows = self.window_scan_thread is not None and self.window_scan_thread.isRunning()
        target_ready = self._validate_current_target(show_message=False)
        controls_ready = not scanning_windows and target_ready

        # 当前产品只自动采集梦幻西游，不再让用户选择采集方式或窗口列表。
        self.start_button.setEnabled(controls_ready)
        self.stop_button.setEnabled(False)

        if scanning_windows:
            tooltip = "正在检测梦幻西游窗口，请稍候"
            text = "正在检测梦幻西游窗口…"
        else:
            hwnd = self._current_window_hwnd()
            if hwnd is None:
                tooltip = "未检测到梦幻西游窗口；程序会继续自动检测"
                text = "未检测到梦幻西游窗口"
            elif not is_window_available(hwnd):
                tooltip = "梦幻西游窗口已经关闭，程序会自动重新检测"
                text = "游戏窗口已关闭"
            elif is_window_minimized(hwnd):
                tooltip = "窗口已最小化：程序会保持采集会话；游戏继续渲染时可后台采集"
                text = f"{self.selected_window_name}（已最小化，保持采集会话）"
            else:
                tooltip = "开始实时采集和分析"
                text = self.selected_window_name
        self.window_text_label.setText(text)
        self.target_label.setText(text)
        self.start_button.setToolTip(tooltip)

    def _refresh_occlusion_status_async(self) -> None:
        if not self._uses_window_capture():
            self.occlusion_label.setText("不适用")
            return
        hwnd = self._current_window_hwnd()
        if hwnd is None:
            self.occluded_state_label.setText("● 窗口被遮挡")
            self.occluded_state_label.setToolTip("")
            self.occlusion_label.setText("等待选择窗口")
            return
        if self.occlusion_check_thread is not None and self.occlusion_check_thread.isRunning():
            return
        self.occlusion_check_thread = OcclusionCheckThread(hwnd, self)
        self.occlusion_check_thread.status_ready.connect(self._on_occlusion_status_ready)
        self.occlusion_check_thread.failed.connect(self._on_occlusion_status_failed)
        self.occlusion_check_thread.finished.connect(self._on_occlusion_check_finished)
        self.occlusion_check_thread.finished.connect(self.occlusion_check_thread.deleteLater)
        self.occlusion_check_thread.start()

    @Slot(object)
    def _on_occlusion_status_ready(self, status: WindowOcclusionStatus) -> None:
        if status.hwnd != self._current_window_hwnd() or not self._uses_window_capture():
            return
        if status.minimized:
            self.current_occluded = False
            # 遮挡检测是独立于采集线程的异步状态检查，不能在采集正常进行时
            # 把“采集中”覆盖成“未采集”。真正的采集状态由 _refresh_status
            # 根据 captured_frames 判断。
            if not self.running:
                self._set_capture_state("not_captured")
            self.occluded_state_label.setText("● 窗口被遮挡")
            self.occlusion_label.setText("窗口已最小化")
            self.occlusion_label.setStyleSheet("color:#d97706;font-weight:600;")
            self.last_occlusion_warning_hwnd = None
            return

        percent = status.ratio * 100
        if status.ratio >= OCCLUSION_WARNING_RATIO:
            self.current_occluded = True
            self._set_capture_state("occluded")
            names = [item.display_name for item in status.occluders[:3]]
            detail = "、".join(names) if names else "其他窗口"
            message = f"⚠ 游戏窗口被遮挡 {percent:.1f}%，请移动或最小化遮挡窗口。遮挡来源：{detail}"
            self.occluded_state_label.setText(f"● 窗口被遮挡 {percent:.1f}%")
            self.occlusion_label.setText(f"检测到遮挡 {percent:.1f}%")
            self.occlusion_label.setStyleSheet("color:#dc2626;font-weight:700;")
            self.occlusion_label.setToolTip(message)
            self.occluded_state_label.setToolTip(message)
            if self.last_occlusion_warning_hwnd != status.hwnd:
                self.log.appendPlainText(message)
                self.last_occlusion_warning_hwnd = status.hwnd
        else:
            self.current_occluded = False
            # 这里不能无条件设置“未采集”：遮挡线程每秒异步返回一次，
            # 如果在采集线程已经收到新帧后返回，就会把“采集中”错误覆盖掉。
            # 采集中的状态统一由 _refresh_status 根据 captured_frames 更新。
            if not self.running:
                self._set_capture_state("not_captured")
            self.occluded_state_label.setText("● 窗口被遮挡")
            self.occlusion_label.setText("未遮挡" if not status.occluded else f"轻微重叠 {percent:.1f}%")
            self.occlusion_label.setStyleSheet("color:#15803d;font-weight:600;")
            self.occlusion_label.setToolTip("")
            self.occluded_state_label.setToolTip("")
            if self.last_occlusion_warning_hwnd == status.hwnd:
                self.log.appendPlainText("游戏窗口遮挡已解除，画面可以继续正常采集。")
            self.last_occlusion_warning_hwnd = None

    @Slot(str)
    def _on_occlusion_status_failed(self, message: str) -> None:
        if self._uses_window_capture():
            self.occluded_state_label.setText("● 窗口被遮挡")
            self.occlusion_label.setText("检测失败")
            self.occlusion_label.setToolTip(message)
            self.occlusion_label.setStyleSheet("color:#d97706;")

    @Slot()
    def _on_occlusion_check_finished(self) -> None:
        if self.sender() is self.occlusion_check_thread:
            self.occlusion_check_thread = None

    @Slot()
    def _start_automatically(self) -> None:
        if not self._validate_current_target(show_message=False):
            self.auto_start_enabled = True
            self.auto_start_in_progress = False
            return
        self._start()

    def _start(self) -> None:
        if self.running or not self._validate_current_target(show_message=True):
            return
        self.last_captured = 0
        self.last_output_path = Path(self.config.output.event_log)
        self.events.setRowCount(0)
        if self._uses_window_capture():
            self.log.appendPlainText(f"正在启动 Windows 窗口采集：{self.selected_window_name}")
        self.thread = QThread(self)
        self.worker = RealtimeWorker(
            self._current_source(),
            0,
            self._current_window_hwnd(),
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
        self.auto_start_enabled = False
        self.auto_start_in_progress = False
        if self.worker is not None:
            self.log.appendPlainText("正在暂停…")
            self.worker.stop()

    @Slot()
    def _on_started(self) -> None:
        self._set_capture_state("not_captured")
        self.capture_label.setText("已启动，等待画面")
        self.output_label.setText("输出通道已开启")
        if self.auto_start_in_progress:
            message = f"✅ 已检测到梦幻西游窗口，已开始自动采集：{self.selected_window_name}"
            self.game_status_banner.setText(message)
            self.game_status_banner.setStyleSheet(
                "background:#14532d;color:#ffffff;border:1px solid #22c55e;padding:8px;font-weight:600;"
            )
            self.log.appendPlainText(message)
            self.auto_start_in_progress = False

    @Slot()
    def _on_frame(self) -> None:
        if self.worker is None:
            return
        image = self.worker.take_latest_preview()
        if image is None:
            return
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image_qt = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
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

    @Slot(object)
    def _on_event(self, event: Event) -> None:
        row = self.events.rowCount()
        self.events.insertRow(row)
        values = [event.timestamp, event.type, f"{event.confidence:.2f}", str(event.payload)]
        for column, value in enumerate(values):
            self.events.setItem(row, column, QTableWidgetItem(value))
        self.events.scrollToBottom()
        self.log.appendPlainText(f"{event.timestamp} {event.type} {event.payload}")
        self.output_label.setText("正在输出事件")

    def _refresh_status(self) -> None:
        self._refresh_occlusion_status_async()
        hwnd = self._current_window_hwnd()
        has_valid_game_window = bool(
            hwnd and is_window_available(hwnd) and self._is_game_window_title(get_window_title(hwnd))
        )
        if self._uses_window_capture() and not self.running and not has_valid_game_window and self.auto_start_enabled:
            self.missing_game_scan_ticks += 1
            if self.missing_game_scan_ticks >= 3:
                self.missing_game_scan_ticks = 0
                self._refresh_windows(quiet=True)
        self._update_start_availability()
        if self.worker is None or self.worker.analyzer is None or not self.running:
            return
        stats = self.worker.analyzer.stats
        self.metrics_label.setText(
            f"采集 {stats.captured_frames} | 分析 {stats.analyzed_frames} | "
            f"丢弃 {stats.dropped_frames} | 采集 {stats.capture_fps:.1f} FPS | 分析 {stats.analysis_fps:.1f} FPS"
        )
        received_new_frame = stats.captured_frames > self.last_captured
        if received_new_frame:
            self._set_capture_state("captured")
            if self._uses_window_capture() and is_window_minimized(self._current_window_hwnd() or 0):
                self.capture_label.setText("最小化后台采集中")
            else:
                self.capture_label.setText("正常采集")
            self.last_captured = stats.captured_frames
        elif self.current_occluded:
            self._set_capture_state("occluded")
            self.capture_label.setText("窗口被遮挡，等待调整")
        elif self._uses_window_capture() and is_window_minimized(self._current_window_hwnd() or 0):
            self._set_capture_state("not_captured")
            self.capture_label.setText("窗口已最小化，等待游戏后台画面")
        else:
            self._set_capture_state("not_captured")
            self.capture_label.setText("等待新画面")
        if self.last_output_path is not None and self.last_output_path.exists():
            self.output_label.setText("输出文件已打开")

    @Slot(object)
    def _on_finished(self, stats: LiveStats) -> None:
        self.log.appendPlainText(
            f"实时分析结束：采集 {stats.captured_frames}，分析 {stats.analyzed_frames}，丢弃 {stats.dropped_frames}"
        )

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._set_capture_state("not_captured")
        self.log.appendPlainText(f"实时分析失败：{message}")
        # 详细堆栈写入运行动态，弹窗只显示首行，避免错误信息过长遮挡整个界面。
        summary = message.strip().splitlines()[0] if message.strip() else "未知错误"
        QMessageBox.critical(self, "实时分析失败", summary)

    @Slot()
    def _on_thread_finished(self) -> None:
        self.running = False
        self._set_capture_state("not_captured")
        self.stop_button.setEnabled(False)
        self.capture_label.setText("已停止")
        self.output_label.setText("已关闭")
        self.worker = None
        self.thread = None
        self._update_start_availability()

    def closeEvent(self, event: Any) -> None:
        if self.worker is not None:
            self.worker.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(3000)
        for scan_thread in (
            self.window_scan_thread,
            self.occlusion_check_thread,
        ):
            if scan_thread is not None and scan_thread.isRunning():
                scan_thread.requestInterruption()
                scan_thread.wait(3000)
        event.accept()


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

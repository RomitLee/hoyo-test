"""CLI for Windows-window capture, compatible video inputs, and offline replay."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

from .capture import list_video_devices, make_source
from .config import AppConfig, load_config
from .equipment_detector import OpenCVTooltipDetector, TooltipDetectorConfig
from .event_machine import EventMachine
from .evidence import EvidenceWriter
from .models import FramePacket
from .perception import RuleBasedPerception, TemplateSpec
from .realtime import RealtimeAnalyzer
from .sampler import AdaptiveSampler, SamplingProfile
from .storage import CompositeEventSink, ConsoleEventSink, JsonlEventSink
from .wgc import list_capturable_windows
from .yolo_detector import YOLOTooltipDetector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoyo-analyzer", description="梦幻西游-希联文超助手：Windows 游戏窗口实时视频行为分析 MVP"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("analyze-video", help="离线回放分析已录制的视频")
    p.add_argument("input", type=Path)
    p.add_argument("--config", type=Path)
    p.add_argument("--fps", type=float, default=None)
    p = sub.add_parser("live", help="实时读取 Windows 游戏窗口或兼容视频输入")
    p.add_argument("--device", type=int, default=0, help="OpenCV 视频设备编号")
    p.add_argument(
        "--source",
        choices=("windows-graphics-capture", "capture-card", "camera", "screen"),
        default="windows-graphics-capture",
    )
    p.add_argument("--window-hwnd", type=lambda value: int(value, 0), help="WGC目标窗口句柄，支持十进制或0x十六进制")
    p.add_argument("--window-title", help="WGC目标窗口标题关键字；未传--window-hwnd时使用")
    p.add_argument("--config", type=Path)
    p.add_argument("--max-seconds", type=float, default=None, help="调试用：运行指定秒数后自动退出")
    p.add_argument("--queue-size", type=int, default=None, help="覆盖实时队列长度")
    sub.add_parser("desktop", help="启动 Windows 桌面界面")
    p = sub.add_parser("list-devices", help="枚举 OpenCV 可读取的视频设备编号")
    p.add_argument("--max-index", type=int, default=10)
    sub.add_parser("list-windows", help="枚举 Windows Graphics Capture 可选择的窗口")
    p = sub.add_parser("extract-frames", help="从视频抽取调试图片")
    p.add_argument("input", type=Path)
    p.add_argument("--config", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("runtime/frames"))
    p.add_argument("--fps", type=float, default=5.0)
    return parser


def _make_sampler(config: AppConfig, fps: float | None = None) -> AdaptiveSampler:
    profile = SamplingProfile(
        config.sampling.normal_fps,
        config.sampling.battle_fps,
        config.sampling.burst_fps,
        config.sampling.burst_duration_ms,
    )
    if fps is not None:
        profile.normal_fps = fps
    return AdaptiveSampler(profile)


def _resolve_project_path(value: str) -> Path:
    """Resolve project-relative model/template paths without changing old configs."""
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    project_candidate = Path(__file__).resolve().parents[2] / candidate
    return project_candidate if project_candidate.exists() else candidate


def _make_perception(config: AppConfig) -> RuleBasedPerception:
    templates = [
        TemplateSpec(signal, str(_resolve_project_path(path)), threshold=0.78, multiscale=signal == "inventory_open")
        for signal, path in config.templates.items()
    ]
    equipment_detector = None
    if config.equipment.enabled:
        backend = config.equipment.backend.casefold().strip()
        if backend == "opencv":
            equipment_detector = OpenCVTooltipDetector(
                TooltipDetectorConfig(
                    min_score=config.equipment.min_score,
                    stable_frames=config.equipment.stable_frames,
                    require_title=config.equipment.require_title,
                    min_title_ratio=config.equipment.min_title_ratio,
                    min_dark_ratio=config.equipment.min_dark_ratio,
                    max_bright_ratio=config.equipment.max_bright_ratio,
                    max_color_ratio=config.equipment.max_color_ratio,
                    min_border_score=config.equipment.min_border_score,
                    min_text_lines=config.equipment.min_text_lines,
                    allow_generic_fallback=config.equipment.allow_generic_fallback,
                )
            )
        else:
            equipment_detector = YOLOTooltipDetector(
                model_path=_resolve_project_path(config.equipment.model_path),
                confidence=config.equipment.confidence,
                image_size=config.equipment.image_size,
                device=config.equipment.device,
                stable_frames=config.equipment.stable_frames,
            )
    return RuleBasedPerception(templates, equipment_detector=equipment_detector)


def _make_sink(config: AppConfig) -> CompositeEventSink:
    sinks: list[object] = [JsonlEventSink(config.output.event_log)]
    if config.output.console:
        sinks.append(ConsoleEventSink())
    return CompositeEventSink(sinks)


def analyze_packets(packets: Iterable[FramePacket], config: AppConfig, fps: float | None = None) -> int:
    sampler, perception, machine = _make_sampler(config, fps), _make_perception(config), EventMachine()
    evidence = EvidenceWriter(config.evidence.directory, config.evidence.pre_buffer_ms)
    sink = _make_sink(config)
    last = None
    processed = 0
    try:
        for packet in sampler.sample(packets):
            processed += 1
            evidence.add(packet)
            observation = perception.observe(packet)
            last = observation
            for event in machine.update(observation):
                evidence.save(event, packet)
                evidence.save_equipment_tooltip(event, packet, config.equipment.output_directory)
                sink.write(event)
        if last is not None:
            for event in machine.close(last):
                sink.write(event)
    finally:
        sink.close()
    return processed


def run_extract_frames(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    source = make_source("video", str(args.input), 0, "extract")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        import cv2

        for packet in _make_sampler(config, args.fps).sample(source):
            cv2.imwrite(str(args.output_dir / f"{packet.frame_index:08d}_{packet.timestamp_ms:012d}.jpg"), packet.image)
            count += 1
    except ImportError as exc:
        raise RuntimeError("需要安装 opencv-python 才能抽帧") from exc
    finally:
        source.close()
    print(f"已抽取 {count} 帧到 {args.output_dir}")
    return 0


def _resolve_window_hwnd(args: argparse.Namespace) -> int | None:
    if args.source != "windows-graphics-capture":
        return None
    if args.window_hwnd is not None:
        return args.window_hwnd
    keyword = (args.window_title or "").strip().casefold()
    if not keyword:
        raise ValueError("Windows窗口采集需要 --window-hwnd 或 --window-title；也可以使用 desktop 图形界面选择窗口")
    matches = [window for window in list_capturable_windows() if keyword in window.title.casefold()]
    if not matches:
        raise ValueError(f"没有找到标题包含 {args.window_title!r} 的窗口")
    if len(matches) > 1:
        names = "、".join(f"{item.title}(0x{item.hwnd:X})" for item in matches[:5])
        raise ValueError(f"找到多个匹配窗口，请改用 --window-hwnd 指定：{names}")
    return matches[0].hwnd


def run_live(args: argparse.Namespace, config: AppConfig) -> int:
    if args.queue_size is not None:
        if args.queue_size < 1:
            raise ValueError("--queue-size 必须大于 0")
        config.sampling.queue_size = args.queue_size
    source = make_source(
        args.source,
        config.capture.path,
        args.device,
        args.source,
        window_hwnd=_resolve_window_hwnd(args),
    )
    sink = _make_sink(config)
    analyzer = RealtimeAnalyzer(
        source,
        config,
        _make_perception(config),
        EventMachine(),
        sink,
        EvidenceWriter(config.evidence.directory, config.evidence.pre_buffer_ms),
    )
    try:
        stats = analyzer.run(args.max_seconds)
    except KeyboardInterrupt:
        analyzer.request_stop()
        print("已停止实时分析")
        return 0
    print(
        f"实时分析结束：采集 {stats.captured_frames} 帧，分析 {stats.analyzed_frames} 帧，丢弃 {stats.dropped_frames} 帧，分析速率 {stats.analysis_fps:.2f} FPS"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "desktop":
        from .gui import main as gui_main

        return gui_main()
    if args.command == "list-devices":
        devices = list_video_devices(args.max_index)
        print("可用视频设备：" + (", ".join(map(str, devices)) if devices else "无"))
        return 0
    if args.command == "list-windows":
        windows = list_capturable_windows()
        if not windows:
            print("没有发现可采集窗口")
        for window in windows:
            state = " [已最小化]" if window.minimized else ""
            print(f"0x{window.hwnd:X}	{window.title}{state}")
        return 0
    config = load_config(getattr(args, "config", None))
    if args.command == "analyze-video":
        source = make_source("video", str(args.input), 0, "video")
        try:
            count = analyze_packets(source, config, args.fps)
        finally:
            source.close()
        print(f"已处理 {count} 个分析帧，事件日志：{config.output.event_log}")
        return 0
    if args.command == "live":
        return run_live(args, config)
    return run_extract_frames(args)


if __name__ == "__main__":
    raise SystemExit(main())

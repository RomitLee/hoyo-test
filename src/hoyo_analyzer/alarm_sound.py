"""Generate a local WAV melody used for persistent slave alarms."""

from __future__ import annotations

import math
import os
import sys
import tempfile
import wave
from array import array
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

from .paths import application_root

SAMPLE_RATE = 22_050
ALARM_FILENAME = "inventory-alarm-loop-v1.wav"
CUSTOM_ALARM_PATH = Path("assets/sounds/inventory_alert.wav")

# An original, short alarm motif. Silence between notes makes the loop easier
# to notice than a single uninterrupted tone.
ALARM_NOTES: tuple[tuple[int, float], ...] = (
    (880, 0.24),
    (0, 0.06),
    (660, 0.24),
    (0, 0.06),
    (880, 0.24),
    (0, 0.12),
    (988, 0.32),
    (0, 0.08),
    (784, 0.32),
    (0, 0.18),
)


def ensure_alarm_sound_file() -> Path:
    """Return a playable WAV path, creating the built-in melody if needed."""

    custom = application_root() / CUSTOM_ALARM_PATH
    if custom.exists() and custom.stat().st_size > 44:
        return custom
    preferred = application_root() / "runtime" / ALARM_FILENAME
    try:
        return _ensure_wav(preferred)
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "hoyo-analyzer" / ALARM_FILENAME
        return _ensure_wav(fallback)


def play_alarm_loop() -> Path:
    """Start streaming the configured WAV until ``stop_alarm_loop`` is called."""

    path = ensure_alarm_sound_file()
    stop_alarm_loop()
    try:
        _initialize_mixer()
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.set_volume(1.0)
        pygame.mixer.music.play(loops=-1)
        if not pygame.mixer.music.get_busy():
            raise pygame.error("播放器启动后未进入播放状态")
    except pygame.error as exc:
        stop_alarm_loop()
        raise OSError(f"无法播放警告音乐：{exc}") from exc
    return path


def stop_alarm_loop() -> None:
    """Stop and release the streaming alarm device, if it is open."""

    if not pygame.mixer.get_init():
        return
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
    except pygame.error:
        pass
    finally:
        pygame.mixer.quit()


def alarm_playback_status() -> str:
    """Return whether the streaming player is actively playing."""

    if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
        return "playing"
    return "stopped"


def _initialize_mixer() -> None:
    """Open a real Windows audio backend, falling back from WASAPI to DirectSound."""

    configured_driver = os.environ.get("SDL_AUDIODRIVER")
    drivers: tuple[str | None, ...] = (configured_driver,) if configured_driver else (None, "directsound")
    errors: list[str] = []
    for driver in drivers:
        if driver is None:
            os.environ.pop("SDL_AUDIODRIVER", None)
            label = "系统默认"
        else:
            os.environ["SDL_AUDIODRIVER"] = driver
            label = driver
        try:
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=1024)
            return
        except pygame.error as exc:
            pygame.mixer.quit()
            errors.append(f"{label}: {exc}")
    if configured_driver is None:
        os.environ.pop("SDL_AUDIODRIVER", None)
    raise pygame.error("；".join(errors))


def _ensure_wav(path: Path) -> Path:
    if path.exists() and path.stat().st_size > 44:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    samples = _melody_samples()
    with wave.open(str(temporary), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples)
    temporary.replace(path)
    return path


def _melody_samples() -> bytes:
    pcm = array("h")
    amplitude = 13_000
    fade_samples = max(1, round(SAMPLE_RATE * 0.008))
    for frequency, duration in ALARM_NOTES:
        sample_count = max(1, round(SAMPLE_RATE * duration))
        if frequency <= 0:
            pcm.extend([0] * sample_count)
            continue
        for index in range(sample_count):
            fade_in = min(1.0, index / fade_samples)
            fade_out = min(1.0, (sample_count - index - 1) / fade_samples)
            envelope = max(0.0, min(fade_in, fade_out))
            value = amplitude * envelope * math.sin(2.0 * math.pi * frequency * index / SAMPLE_RATE)
            pcm.append(round(value))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()

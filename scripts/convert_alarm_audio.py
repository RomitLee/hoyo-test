"""Convert a FLAC alarm track to a compact, Win32-compatible PCM WAV."""

from __future__ import annotations

import argparse
from pathlib import Path

import soundfile as sf

TARGET_SAMPLE_RATE = 22_050
BLOCK_FRAMES = 262_144


def convert(source: Path, target: Path) -> None:
    with sf.SoundFile(source) as input_file:
        if input_file.samplerate % TARGET_SAMPLE_RATE != 0:
            raise ValueError(
                f"输入采样率 {input_file.samplerate} 不能无损整数降采样到 {TARGET_SAMPLE_RATE}，请使用 FFmpeg 转换。"
            )
        step = input_file.samplerate // TARGET_SAMPLE_RATE
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        global_offset = 0
        with sf.SoundFile(
            temporary,
            mode="w",
            samplerate=TARGET_SAMPLE_RATE,
            channels=1,
            format="WAV",
            subtype="PCM_16",
        ) as output_file:
            while True:
                block = input_file.read(BLOCK_FRAMES, dtype="float32", always_2d=True)
                if not len(block):
                    break
                mono = block.mean(axis=1)
                first = (-global_offset) % step
                output_file.write(mono[first::step])
                global_offset += len(block)
        temporary.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    convert(args.source, args.target)
    info = sf.info(args.target)
    print(
        f"Converted {args.source} -> {args.target} "
        f"({info.samplerate} Hz, {info.channels} channel, {info.duration:.1f} seconds)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

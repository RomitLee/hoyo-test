"""External file replacement and rollback logic for Windows onedir builds."""

from __future__ import annotations

import argparse
import ctypes
import json
import hashlib
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


class InstallError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or ".." in parts or ":" in parts[0]:
                raise InstallError("更新安装包包含不安全路径")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise InstallError("更新安装包不能包含符号链接")
        package.extractall(destination)


def _move_children(source: Path, destination: Path, *, exclude_runtime: bool = False) -> list[str]:
    moved: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if exclude_runtime and child.name.casefold() == "runtime":
            continue
        target = destination / child.name
        if target.exists():
            raise InstallError(f"更新目标冲突：{child.name}")
        shutil.move(str(child), str(target))
        moved.append(child.name)
    return moved


def _remove_children(root: Path, names: list[str]) -> None:
    for name in names:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)


def replace_application(app_root: Path, payload_root: Path, backup_root: Path) -> list[str]:
    """Replace all packaged files while preserving app_root/runtime."""

    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True)
    old_names = _move_children(app_root, backup_root, exclude_runtime=True)
    new_names: list[str] = []
    try:
        for child in payload_root.iterdir():
            if child.name.casefold() == "runtime":
                continue
            target = app_root / child.name
            if target.exists():
                raise InstallError(f"更新目标冲突：{child.name}")
            shutil.move(str(child), str(target))
            new_names.append(child.name)
    except Exception:
        _remove_children(app_root, new_names)
        _move_children(backup_root, app_root)
        raise
    return old_names


def rollback_application(app_root: Path, backup_root: Path) -> None:
    current_names = [child.name for child in app_root.iterdir() if child.name.casefold() != "runtime"]
    _remove_children(app_root, current_names)
    _move_children(backup_root, app_root)


def wait_for_process(pid: int, timeout_seconds: int = 90) -> None:
    if sys.platform == "win32":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _launch(executable: Path, health_file: Path | None = None) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    if health_file is not None:
        environment["HOYO_UPDATE_HEALTH_FILE"] = str(health_file)
    else:
        environment.pop("HOYO_UPDATE_HEALTH_FILE", None)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([str(executable)], cwd=executable.parent, env=environment, creationflags=flags)


def perform_update(args: argparse.Namespace) -> int:
    app_root = Path(args.app_root).resolve()
    archive = Path(args.archive).resolve()
    updates_root = (app_root / "runtime" / "updates").resolve()
    try:
        archive.relative_to(updates_root)
    except ValueError as exc:
        raise InstallError("更新安装包不在受信任的运行目录") from exc
    if file_sha256(archive) != args.expected_sha256.lower():
        raise InstallError("更新安装包 SHA-256 校验失败")

    wait_for_process(args.pid)
    token = uuid.uuid4().hex
    extracted = updates_root / f"extracted-{token}"
    backup = updates_root / f"backup-{args.old_version}-{token}"
    health_file = updates_root / f"health-{token}.ok"
    safe_extract(archive, extracted)
    payload_root = extracted / args.archive_root
    replacement_executable = payload_root / args.executable
    if not replacement_executable.is_file():
        raise InstallError("更新安装包缺少主程序")

    replace_application(app_root, payload_root, backup)
    installed_executable = app_root / args.executable
    process: subprocess.Popen[bytes] | None = None
    try:
        process = _launch(installed_executable, health_file)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if health_file.is_file():
                shutil.rmtree(backup, ignore_errors=True)
                shutil.rmtree(extracted, ignore_errors=True)
                archive.unlink(missing_ok=True)
                (updates_root / f"install-failed-{args.new_version}.json").unlink(missing_ok=True)
                return 0
            if process.poll() is not None:
                break
            time.sleep(0.25)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        rollback_application(app_root, backup)
        raise InstallError("新版本启动校验失败，已自动恢复旧版本")
    finally:
        health_file.unlink(missing_ok=True)



def record_failed_install(updates_root: Path, version: str | None) -> None:
    """Remember a rolled-back install so the client can back off instead of looping."""
    if not version:
        return
    path = updates_root / f"install-failed-{version}.json"
    attempts = 1
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        attempts = int(previous.get("attempts", 0)) + 1
    except (OSError, ValueError, TypeError):
        attempts = 1
    try:
        updates_root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": version, "attempts": attempts,
                        "at": datetime.now(UTC).isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
    except OSError:
        pass

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--app-root")
    parser.add_argument("--archive")
    parser.add_argument("--archive-root")
    parser.add_argument("--executable")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--old-version")
    parser.add_argument("--new-version")
    parser.add_argument("--pid", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return 0
    required = (
        "app_root", "archive", "archive_root", "executable", "expected_sha256",
        "old_version", "new_version", "pid",
    )
    if any(getattr(args, name) in (None, "") for name in required):
        raise InstallError("缺少更新参数")
    try:
        return perform_update(args)
    except Exception as exc:  # noqa: BLE001 - updater must persist every failure
        record_failed_install(Path(args.app_root).resolve() / "runtime" / "updates", args.new_version)
        try:
            root = Path(args.app_root).resolve()
            log = root / "runtime" / "updates" / "update.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"自动更新失败：{type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        try:
            executable = Path(args.app_root).resolve() / args.executable
            if executable.is_file():
                _launch(executable)
        except (OSError, TypeError):
            pass
        return 1

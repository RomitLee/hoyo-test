"""Signed, unattended desktop update checks for packaged Windows clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from . import __version__
from .paths import application_root

UPDATE_MANIFEST_URL = "https://jiankong.maipi.top/api/v1/updates/latest"
UPDATE_INTERVAL_MS = 10 * 60 * 1000
UPDATER_EXECUTABLE = "梦幻西游自动更新.exe"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
ClientRole = Literal["slave", "master"]


class UpdateError(RuntimeError):
    """Raised when an update is invalid or cannot be prepared safely."""


def parse_version(value: str) -> tuple[int, int, int]:
    text = value.strip()
    if text[:1].lower() == "v":
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid application version: {value!r}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def manifest_signature_payload(document: dict[str, Any]) -> bytes:
    """Return the stable byte representation signed by the release key."""

    lines = ["hoyo-update-v1", str(document.get("schema_version")), str(document.get("client_version"))]
    packages = document.get("packages")
    if not isinstance(packages, dict):
        raise UpdateError("更新清单缺少安装包信息")
    for role in ("slave", "master"):
        package = packages.get(role)
        if not isinstance(package, dict):
            raise UpdateError(f"更新清单缺少 {role} 安装包")
        lines.extend(
            [
                role,
                str(package.get("filename")),
                str(package.get("sha256")).lower(),
                str(package.get("size")),
                str(package.get("download_path")),
                str(package.get("archive_root")),
                str(package.get("executable")),
            ]
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _load_public_key() -> tuple[int, int]:
    key_path = application_root() / "assets" / "update-public-key.json"
    try:
        key = json.loads(key_path.read_text(encoding="utf-8"))
        if key.get("algorithm") != "RSA-3072-SHA256":
            raise ValueError("unsupported algorithm")
        modulus = int.from_bytes(base64.b64decode(key["modulus"], validate=True), "big")
        exponent = int.from_bytes(base64.b64decode(key["exponent"], validate=True), "big")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError("更新公钥无效") from exc
    if modulus.bit_length() < 3072 or exponent < 3:
        raise UpdateError("更新公钥强度不足")
    return modulus, exponent


def verify_manifest_signature(document: dict[str, Any], public_key: tuple[int, int] | None = None) -> bool:
    signature_info = document.get("signature")
    if not isinstance(signature_info, dict) or signature_info.get("algorithm") != "RSA-3072-SHA256":
        return False
    try:
        signature = base64.b64decode(signature_info["value"], validate=True)
        modulus, exponent = public_key or _load_public_key()
        key_size = (modulus.bit_length() + 7) // 8
        if len(signature) != key_size:
            return False
        encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(key_size, "big")
        digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(manifest_signature_payload(document)).digest()
        padding_size = key_size - len(digest_info) - 3
        if padding_size < 8:
            return False
        expected = b"\x00\x01" + (b"\xff" * padding_size) + b"\x00" + digest_info
        return hmac.compare_digest(encoded, expected)
    except (KeyError, TypeError, ValueError, UpdateError):
        return False


def select_update(document: dict[str, Any], role: ClientRole, current_version: str) -> dict[str, Any] | None:
    if document.get("schema_version") != 2 or not verify_manifest_signature(document):
        raise UpdateError("更新清单签名校验失败")
    release_version = str(document.get("client_version") or "")
    try:
        if parse_version(release_version) <= parse_version(current_version):
            return None
    except ValueError as exc:
        raise UpdateError("更新清单版本号无效") from exc

    packages = document.get("packages")
    package = packages.get(role) if isinstance(packages, dict) else None
    if not isinstance(package, dict):
        raise UpdateError("更新清单没有当前程序的安装包")
    required_strings = ("filename", "sha256", "download_path", "archive_root", "executable")
    if any(not isinstance(package.get(key), str) or not package[key] for key in required_strings):
        raise UpdateError("更新清单安装包字段无效")
    digest = str(package["sha256"]).lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise UpdateError("更新清单 SHA-256 无效")
    size = package.get("size")
    if not isinstance(size, int) or size <= 0 or size > MAX_PACKAGE_BYTES:
        raise UpdateError("更新安装包大小无效")
    download_path = str(package["download_path"])
    if not download_path.startswith("/api/v1/updates/download/") or "://" in download_path:
        raise UpdateError("更新下载地址无效")
    return {**package, "client_version": release_version}


def _write_update_log(message: str) -> None:
    try:
        log_path = application_root() / "runtime" / "updates" / "update.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


def mark_update_healthy() -> None:
    """Acknowledge to the external updater that the replacement UI started."""

    raw_path = os.environ.pop("HOYO_UPDATE_HEALTH_FILE", "").strip()
    if not raw_path:
        return
    try:
        path = Path(raw_path).resolve()
        updates_root = (application_root() / "runtime" / "updates").resolve()
        path.relative_to(updates_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__version__, encoding="utf-8")
    except (OSError, ValueError):
        _write_update_log("更新成功标记写入失败")


class UpdateController(QObject):
    """Check, download and hand a verified package to the external updater."""

    _check_finished = Signal(object, str)

    def __init__(self, role: ClientRole, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self._checking = False
        self._installing = False
        self._check_finished.connect(self._on_check_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(UPDATE_INTERVAL_MS)
        self.timer.timeout.connect(self.check_now)
        self.timer.start()
        QTimer.singleShot(1000, self.check_now)

    @Slot()
    def check_now(self) -> None:
        if self._checking or self._installing:
            return
        self._checking = True
        threading.Thread(target=self._check_in_background, name="hoyo-update-check", daemon=True).start()

    def _check_in_background(self) -> None:
        try:
            package = self._fetch_and_prepare()
            self._check_finished.emit(package, "")
        except Exception as exc:  # noqa: BLE001 - update failures must never terminate monitoring
            self._check_finished.emit(None, f"{type(exc).__name__}: {exc}")

    def _fetch_and_prepare(self) -> dict[str, Any] | None:
        request = Request(
            UPDATE_MANIFEST_URL,
            headers={"Accept": "application/json", "User-Agent": f"HoyoMonitor/{__version__}"},
        )
        with urlopen(request, timeout=15) as response:
            raw_manifest = response.read(MAX_MANIFEST_BYTES + 1)
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise UpdateError("更新清单过大")
        document = json.loads(raw_manifest.decode("utf-8-sig"))
        if not isinstance(document, dict):
            raise UpdateError("更新清单格式无效")
        package = select_update(document, self.role, __version__)
        if package is None:
            return None

        manifest_origin = urlparse(UPDATE_MANIFEST_URL)
        download_url = urljoin(UPDATE_MANIFEST_URL, str(package["download_path"]))
        download_origin = urlparse(download_url)
        if download_origin.scheme != "https" or download_origin.netloc != manifest_origin.netloc:
            raise UpdateError("更新安装包必须来自受信任的 HTTPS 主机")

        root = application_root()
        helper = root / UPDATER_EXECUTABLE
        if not helper.is_file():
            raise UpdateError("自动更新器不存在，请重新安装完整版本")
        download_dir = root / "runtime" / "updates" / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        final_path = download_dir / f"{package['client_version']}-{self.role}.zip"
        temporary_path = final_path.with_suffix(".zip.part")
        digest = hashlib.sha256()
        received = 0
        download_request = Request(download_url, headers={"User-Agent": f"HoyoMonitor/{__version__}"})
        try:
            with urlopen(download_request, timeout=30) as response, temporary_path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > int(package["size"]) or received > MAX_PACKAGE_BYTES:
                        raise UpdateError("更新安装包大小超出清单声明")
                    digest.update(chunk)
                    output.write(chunk)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        if received != int(package["size"]) or digest.hexdigest() != package["sha256"]:
            temporary_path.unlink(missing_ok=True)
            raise UpdateError("更新安装包完整性校验失败")
        temporary_path.replace(final_path)
        return {**package, "archive": str(final_path), "helper": str(helper)}

    @Slot(object, str)
    def _on_check_finished(self, package: object, error: str) -> None:
        self._checking = False
        if error:
            _write_update_log(f"检查更新失败：{error}")
            return
        if not isinstance(package, dict):
            return
        try:
            self._launch_updater(package)
        except (OSError, ValueError, UpdateError) as exc:
            _write_update_log(f"启动更新失败：{type(exc).__name__}: {exc}")

    def _launch_updater(self, package: dict[str, Any]) -> None:
        root = application_root()
        updates_root = root / "runtime" / "updates"
        updates_root.mkdir(parents=True, exist_ok=True)
        launcher = updates_root / f"updater-{uuid.uuid4().hex}.exe"
        shutil.copy2(Path(str(package["helper"])), launcher)
        command = [
            str(launcher),
            "--app-root", str(root),
            "--archive", str(package["archive"]),
            "--archive-root", str(package["archive_root"]),
            "--executable", str(package["executable"]),
            "--expected-sha256", str(package["sha256"]),
            "--old-version", __version__,
            "--new-version", str(package["client_version"]),
            "--pid", str(os.getpid()),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(command, close_fds=True, creationflags=creationflags)
        self._installing = True
        _write_update_log(f"已下载 {package['client_version']}，正在自动安装并重启")
        app = QApplication.instance()
        if app is not None:
            app.quit()


def install_auto_update(role: ClientRole, parent: QObject) -> UpdateController | None:
    """Enable checks for frozen builds; source checkouts never contact production."""

    if not getattr(sys, "frozen", False) and os.environ.get("HOYO_ENABLE_SOURCE_UPDATES") != "1":
        return None
    return UpdateController(role, parent)

"""Private COS storage for immutable, signed-manifest client packages."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .config import Settings

SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_manifest_signature(document: dict) -> bool:
    """Verify the same RSA-3072 signature used by the shipped Windows updater."""
    try:
        signature = document["signature"]
        if signature["algorithm"] != "RSA-3072-SHA256":
            return False
        public = json.loads((Path(__file__).parent / "update-public-key.json").read_text(encoding="utf-8"))
        modulus = int.from_bytes(base64.b64decode(public["modulus"], validate=True), "big")
        exponent = int.from_bytes(base64.b64decode(public["exponent"], validate=True), "big")
        if public["algorithm"] != "RSA-3072-SHA256" or modulus.bit_length() < 3072 or exponent < 3:
            return False
        lines = ["hoyo-update-v1", str(document["schema_version"]), str(document["client_version"])]
        if document["schema_version"] == 3:
            lines.append(str(document["release_role"]))
        for role in ("slave", "master"):
            package = document["packages"][role]
            lines.extend((role, str(package["filename"]), str(package["sha256"]).lower(),
                          str(package["size"]), str(package["download_path"]),
                          str(package["archive_root"]), str(package["executable"])))
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        raw = base64.b64decode(signature["value"], validate=True)
        key_size = (modulus.bit_length() + 7) // 8
        if len(raw) != key_size:
            return False
        encoded = pow(int.from_bytes(raw, "big"), exponent, modulus).to_bytes(key_size, "big")
        digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(payload).digest()
        padding_size = key_size - len(digest_info) - 3
        return padding_size >= 8 and hmac.compare_digest(
            encoded, b"\x00\x01" + b"\xff" * padding_size + b"\x00" + digest_info)
    except (OSError, KeyError, TypeError, ValueError, OverflowError):
        return False


class ReleaseStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return all((self.settings.cos_release_secret_id, self.settings.cos_release_secret_key,
                    self.settings.cos_release_bucket, self.settings.cos_release_region))

    def _client(self):
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(Region=self.settings.cos_release_region,
                           SecretId=self.settings.cos_release_secret_id,
                           SecretKey=self.settings.cos_release_secret_key,
                           Scheme="https", Timeout=30)
        return CosS3Client(config, retry=0)

    @staticmethod
    def object_key(role: str, version: str, package: dict) -> str:
        digest = str(package.get("sha256", ""))
        filename = str(package.get("filename", ""))
        if role not in {"slave", "master"} or not re.fullmatch(r"V\d+\.\d+\.\d+", version):
            raise ValueError("程序类型或版本号无效")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("安装包摘要无效")
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename or not filename.lower().endswith(".zip"):
            raise ValueError("安装包文件名无效")
        return f"releases/{role}/{version}/{digest}/{filename}"

    def exists(self, key: str) -> bool:
        self._client().head_object(Bucket=self.settings.cos_release_bucket, Key=key)
        return True

    def download_url(self, key: str) -> str:
        url = self._client().get_presigned_url(
            Bucket=self.settings.cos_release_bucket, Key=key, Method="GET", Expired=3600)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("COS 下载地址无效")
        return url

    def upload_verified(self, key: str, archive: Path, expected_hash: str, expected_size: int) -> None:
        if not self.configured:
            raise RuntimeError("客户端安装包 COS 存储尚未配置")
        digest = hashlib.sha256()
        size = 0
        with archive.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
                size += len(block)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError("安装包大小或 SHA-256 与清单不一致")
        with archive.open("rb") as source:
            self._client().put_object(Bucket=self.settings.cos_release_bucket, Key=key,
                                      Body=source, ContentType="application/zip",
                                      CacheControl="private, max-age=3600")
        self.exists(key)

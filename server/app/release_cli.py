"""Publish a signed Windows client release to COS from the relay host."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .config import Settings
from .releases import ReleaseStore, verify_manifest_signature


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def publish(settings: Settings, manifest_path: Path, package_path: Path) -> str:
    document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if document.get("schema_version") != 3 or document.get("release_role") != "slave":
        raise ValueError("只发布签名的从机独立更新清单")
    if not verify_manifest_signature(document):
        raise ValueError("更新清单签名无效")
    version = str(document["client_version"])
    package = document["packages"]["slave"]
    store = ReleaseStore(settings)
    key = store.object_key("slave", version, package)
    if package["client_version"] != version or package["download_path"] != f"/api/v1/updates/download/slave/{version}":
        raise ValueError("从机版本或下载路径与清单不一致")
    if package_path.name != package["filename"]:
        raise ValueError("安装包文件名与清单不一致")
    release_root = settings.update_directory
    current = release_root / "release-manifest-slave.json"
    if current.exists():
        old = json.loads(current.read_text(encoding="utf-8-sig"))
        old_version = str(old.get("client_version", ""))
        if old_version == version and old["packages"]["slave"]["sha256"] != package["sha256"]:
            raise ValueError("该版本号已经发布了不同内容，请升级版本号")
        if old_version != version and verify_manifest_signature(old):
            old_archive = release_root / str(old["packages"]["slave"]["filename"])
            history = release_root / "history" / "slave" / old_version
            if old_archive.is_file():
                atomic_copy(old_archive, history / old_archive.name)
            atomic_copy(current, history / "release-manifest.json")
    store.upload_verified(key, package_path, str(package["sha256"]), int(package["size"]))
    history = release_root / "history" / "slave" / version
    atomic_copy(manifest_path, history / "release-manifest.json")
    atomic_copy(manifest_path, current)
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description="将从机安装包发布至私有 COS")
    parser.add_argument("manifest", type=Path, help="已签名的 release-manifest-slave.json")
    parser.add_argument("package", type=Path, help="清单对应的 ZIP 包")
    args = parser.parse_args()
    key = publish(Settings.from_env(), args.manifest, args.package)
    print(f"已发布到 COS：{key}")


if __name__ == "__main__":
    main()

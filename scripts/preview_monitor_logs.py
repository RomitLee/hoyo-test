"""Run the packaged master against an isolated loopback relay and demo account."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "runtime" / "preview-v210"


def run(check: bool = False, check_package: bool = False) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = dict(os.environ, HOYO_DATABASE_PATH=str(DATA / "relay.sqlite3"),
                       HOYO_MASTER_TOKEN=secrets.token_urlsafe(40), HOYO_DEVICE_TOKENS="{}")
    log = (DATA / "server.log").open("a", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
                               "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT / "server",
                              env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    stop = threading.Event()

    def api(path, data=None, token="", device="", method=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if device:
            headers["X-Device-ID"] = device
        request = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                          headers=headers, method=method)
        with urlopen(request, timeout=8) as response:
            return json.load(response)

    try:
        for _ in range(80):
            try:
                assert api("/healthz")["version"] == "2.7.0"
                break
            except (URLError, OSError):
                time.sleep(0.15)
        else:
            raise RuntimeError("本地预览服务启动失败，请查看 runtime/preview-v210/server.log")
        credential_file = DATA / "accounts.json"
        passwords = json.loads(credential_file.read_text()) if credential_file.exists() else {
            "preview210": secrets.token_urlsafe(24), "preview_other": secrets.token_urlsafe(24)}
        credential_file.write_text(json.dumps(passwords), encoding="utf-8")
        tokens = {}
        for username, password in passwords.items():
            credentials = {"username": username, "password": password}
            try:
                tokens[username] = api("/api/v1/auth/login", credentials)["access_token"]
            except HTTPError:
                tokens[username] = api("/api/v1/auth/register", credentials)["access_token"]
        owner = tokens["preview210"]
        code = api("/api/v1/master/pairing-codes", {}, owner)["pairing_code"]
        identity = api("/api/v1/slave/pair", {"pairing_code": code, "installation_id": "local-preview-v210-device",
                                             "hostname": "本地预览从机", "app_version": "V2.0.0"})
        device, secret = identity["device_id"], identity["device_token"]
        now = datetime.now(UTC)
        for index in range(144):
            panel = ("inventory", "give", "trade", "attributes", "stall", "stall_records")[index % 6]
            api("/api/v1/slave/monitor-logs", {"event_id": f"preview-panel-{index}",
                "event_type": "panel_opened" if index % 12 < 6 else "panel_closed", "panel_type": panel,
                "reason": "recognition", "occurred_at": (now - timedelta(minutes=144-index)).isoformat(),
                "window_id": "preview-window", "server_name": "预览区服", "role_name": "测试角色"}, secret, device)

        def report(percentage=85):
            at = datetime.now(UTC).isoformat()
            api("/api/v1/slave/status", {"reported_at": at, "windows": [{"window_id": "preview-window",
                "server_name": "预览区服", "role_name": "测试角色", "status_text": "背包未满",
                "cash": 12179964, "money_readable": True, "money_confirmed_at": at,
                "occlusion_percent": percentage, "updated_at": at}]}, secret, device)

        if check:
            path = "/api/v1/master/monitor-log-settings"
            original = api(path, token=owner)
            try:
                policy = {"occlusion_enabled": True, "occlusion_percent": 80, "occlusion_seconds": 0}
                assert api(path, policy, owner, method="PUT") == policy
                assert api(path, token=tokens["preview_other"])["occlusion_seconds"] == 5
                report(50)
                before = api("/api/v1/master/monitor-logs?event_type=window_occluded", token=owner)["logs"]
                report(85)
                after = api("/api/v1/master/monitor-logs?event_type=window_occluded", token=owner)["logs"]
                assert len(after) == len(before) + 1
                api(path, {**policy, "occlusion_enabled": False}, owner, method="PUT")
                report(99)
                assert api("/api/v1/master/monitor-logs?event_type=window_occluded", token=owner)["logs"] == after
                trade = api("/api/v1/master/monitor-logs?panel_type=trade&limit=10", token=owner)
                assert len(trade["logs"]) == 10 and trade["has_more"]
                assert all(row["panel_type"] == "trade" for row in trade["logs"])
            finally:
                api(path, original, owner, method="PUT")
            print("LOCAL_PREVIEW_POLICY_FILTER_ACCOUNT_ISOLATION_OK", flush=True)
            return

        manifest = json.loads((ROOT / "dist-v210-independent" / "release-manifest.json").read_text(encoding="utf-8-sig"))
        package = manifest["packages"]["master"]
        client = DATA / "client"
        shutil.copytree(ROOT / "dist-v210-independent" / package["archive_root"], client, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("master_settings.json", "slave_settings.json", "monitor-log-outbox.json", "updates"))
        runtime = client / "runtime"
        runtime.mkdir(exist_ok=True)
        (runtime / "master_settings.json").write_text(json.dumps({"access_token": owner, "username": "preview210",
            "poll_seconds": 2, "popup_alert_enabled": False}), encoding="utf-8")
        report()

        def simulate():
            while not stop.wait(5):
                try:
                    report()
                except OSError:
                    return

        threading.Thread(target=simulate, daemon=True).start()
        environment["HOYO_PREVIEW_RELAY_URL"] = base
        environment.pop("QT_QPA_PLATFORM", None)
        if check_package:
            environment["QT_QPA_PLATFORM"] = "offscreen"
            position = (DATA / "server.log").stat().st_size
            child = subprocess.Popen([str(client / package["executable"])], env=environment, cwd=client,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                for _ in range(80):
                    time.sleep(0.2)
                    content = (DATA / "server.log").read_text(encoding="utf-8", errors="replace")[position:]
                    if 'GET /api/v1/master/monitor-log-settings HTTP/1.1" 200' in content and 'GET /api/v1/master/monitor-logs?' in content:
                        print("PACKAGED_MASTER_LOOPBACK_SETTINGS_AND_LOGS_OK", flush=True)
                        break
                    if child.poll() is not None:
                        raise RuntimeError(f"预览客户端提前退出：{child.returncode}")
                else:
                    raise RuntimeError("预览客户端未能读取本地账号规则或日志")
            finally:
                child.terminate()
                child.wait(timeout=10)
            return
        subprocess.run([str(client / package["executable"])], env=environment, cwd=client, check=True)
    finally:
        stop.set()
        server.terminate()
        server.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--check-package", action="store_true")
    options = parser.parse_args()
    try:
        run(options.check, options.check_package)
    except Exception:
        DATA.mkdir(parents=True, exist_ok=True)
        details = traceback.format_exc()
        (DATA / "preview-error.log").write_text(details, encoding="utf-8")
        if options.check or options.check_package:
            raise
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, "本地预览启动失败，请查看 runtime/preview-v210/preview-error.log", "本地预览", 0x10)

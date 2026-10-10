"""Small, dependency-free Bark push client."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BARK_PUSH_URL = "https://api.day.app/push"
EVENT_LABELS = {
    "inventory_full": "背包已满",
    "inventory_capacity": "背包容量不足",
    "inventory_closed": "背包未打开",
    "inventory_occluded": "背包被遮挡",
    "window_occluded": "窗口被遮挡",
    "window_minimized": "游戏窗口最小化",
}


class BarkPushError(RuntimeError):
    """Raised when Bark rejects or cannot receive a push."""


def event_message(event: dict[str, Any]) -> tuple[str, str]:
    event_name = EVENT_LABELS.get(str(event.get("event_type") or ""), "监控告警")
    device_name = event.get("display_name") or event.get("hostname") or event.get("device_id") or "未知从机"
    role = " / ".join(str(value) for value in (event.get("server_name"), event.get("role_name")) if value)
    details = [f"{device_name} · {role or '未识别角色'}", event_name]
    if event.get("empty_slots") is not None:
        details[-1] += f"，剩余 {event['empty_slots']} 格"
    return f"背包监控 · {event_name}", "\n".join(details)


def send_bark_notification(
    device_key: str, *, title: str, body: str, call_enabled: bool,
    target_url: str = "https://jiankong.maipi.top/?tab=alerts",
) -> None:
    payload = {
        "device_key": device_key,
        "title": title,
        "body": body,
        "group": "hoyo-inventory",
        "level": "timeSensitive",
        "url": target_url,
    }
    if call_enabled:
        payload["call"] = "1"
    request = Request(
        BARK_PUSH_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "hoyo-relay/2"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BarkPushError(f"Bark 推送失败：{exc}") from exc
    if int(result.get("code", -1)) != 200:
        raise BarkPushError(str(result.get("message") or "Bark 拒绝了推送请求"))

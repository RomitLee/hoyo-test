"""Panel names shared with the master UI without importing image libraries."""

PANEL_NAMES = {
    "inventory": "背包", "give": "给予面板", "trade": "交易面板",
    "attributes": "属性面板", "stall": "卖货摊位", "stall_buy": "收货摊位",
    "stall_records": "摊位记录面板",
}


def panel_event_name(event_type: str, panel_type: str | None = None) -> str:
    if event_type == "game_default":
        return "关闭面板，返回默认游戏界面"
    if event_type == "panel_target_changed":
        return ("给予" if panel_type == "give" else "交易") + "对象未识别"
    verb = "打开" if event_type == "panel_opened" else "关闭"
    return verb + PANEL_NAMES.get(panel_type or "", "未知面板")


def monitor_event_label(item: dict) -> str:
    event_type = item.get("event_type", "")
    panel = item.get("panel_type")
    base = panel_event_name(event_type, panel)
    if item.get("target_status") in {"recognized", "unrecognized"} and panel in {"give", "trade"}:
        prefix = ("给予" if panel == "give" else "交易") + "对象" if event_type == "panel_target_changed" else base + "，对象"
        detail = (f"{item['target_name']}，{item['target_level']}级"
                  if item["target_status"] == "recognized" and item.get("target_name") and item.get("target_level")
                  else "对象未识别")
        return prefix + "：" + detail
    return item.get("message") or base

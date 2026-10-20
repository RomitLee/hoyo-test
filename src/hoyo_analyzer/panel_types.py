"""Panel names shared with the master UI without importing image libraries."""

PANEL_NAMES = {
    "inventory": "背包", "give": "给予面板", "trade": "交易面板",
    "attributes": "属性面板", "stall": "摊位面板", "stall_records": "摊位记录面板",
}


def panel_event_name(event_type: str, panel_type: str | None = None) -> str:
    if event_type == "game_default":
        return "关闭面板，返回默认游戏界面"
    verb = "打开" if event_type == "panel_opened" else "关闭"
    return verb + PANEL_NAMES.get(panel_type or "", "未知面板")

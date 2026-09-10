from datetime import UTC, datetime

import numpy as np

from hoyo_analyzer.models import Event, FramePacket, event_name_zh, format_beijing_time, format_timestamp


def test_format_timestamp():
    assert format_timestamp(252480) == "00:04:12.480"


def test_frame_packet_timestamp():
    packet = FramePacket(3, 1000, np.zeros((2, 2, 3), dtype=np.uint8))
    assert packet.timestamp == "00:00:01.000"


def test_player_facing_event_names_are_chinese():
    assert event_name_zh("inventory_opened") == "打开背包"
    assert Event("evt_1", 0, "equipment_tooltip_opened").display_name == "显示装备属性"


def test_format_beijing_time_uses_24_hour_clock():
    value = datetime(2026, 9, 7, 17, 30, 11, tzinfo=UTC)
    assert format_beijing_time(value) == "2026-09-08 01:30:11"

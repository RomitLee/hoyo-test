from hoyo_analyzer.config import InventoryMonitorConfig
from hoyo_analyzer.event_machine import EventMachine
from hoyo_analyzer.models import Observation


def obs(i, **signals):
    return Observation(i, i * 100, signals, 0.9)


def test_inventory_requires_two_frames_and_deduplicates():
    machine = EventMachine()
    assert machine.update(obs(0, inventory_open=False)) == []
    assert machine.update(obs(1, inventory_open=True)) == []
    events = machine.update(obs(2, inventory_open=True))
    assert [e.type for e in events] == ["inventory_opened"]
    assert machine.update(obs(3, inventory_open=True)) == []


def test_battle_exit_is_emitted_after_confirmation():
    machine = EventMachine()
    machine.update(obs(0, battle_active=True))
    started = machine.update(obs(1, battle_active=True))
    assert [e.type for e in started] == ["battle_started"]
    machine.update(obs(2, battle_active=False))
    ended = machine.update(obs(3, battle_active=False))
    assert [e.type for e in ended] == ["battle_ended"]


def test_map_change_payload():
    machine = EventMachine()
    events = machine.update(obs(0, map_name="北俱芦洲"))
    assert events[0].type == "map_entered" and events[0].payload["map_name"] == "北俱芦洲"


def test_screen_change_event_keeps_change_score():
    machine = EventMachine()
    machine.update(obs(0, frame_changed={"active": False, "change_score": 0.0}))
    machine.update(obs(1, frame_changed={"active": True, "change_score": 0.2}))
    events = machine.update(obs(2, frame_changed={"active": True, "change_score": 0.35}))

    assert [event.type for event in events] == ["screen_changed"]
    assert events[0].payload == {"change_score": 0.35}


def test_equipment_tooltip_is_blocked_until_inventory_open_event():
    machine = EventMachine()
    tooltip = {"active": True, "score": 0.91, "bbox": [200, 300, 600, 700]}

    # 即使 OpenCV 连续误判出浮窗，只要还没有确认“打开背包”，就不应产生装备事件。
    assert machine.update(obs(0, inventory_open=False, equipment_slot_hover=True, equipment_tooltip=tooltip)) == []
    assert machine.update(obs(1, inventory_open=False, equipment_slot_hover=True, equipment_tooltip=tooltip)) == []

    # 背包信号第一帧尚未确认，仍然不能产生装备属性事件。
    assert machine.update(obs(2, inventory_open=True, equipment_slot_hover=True, equipment_tooltip=tooltip)) == []

    # 第二帧确认打开背包后，才能在同一轮产生装备属性事件。
    events = machine.update(obs(3, inventory_open=True, equipment_slot_hover=True, equipment_tooltip=tooltip))
    assert [event.type for event in events] == ["inventory_opened", "equipment_tooltip_opened"]
    assert machine.state.inventory_open is True


def test_equipment_tooltip_event_keeps_detector_diagnostics():
    machine = EventMachine()
    tooltip = {
        "active": True,
        "score": 0.93,
        "bbox": [200, 300, 600, 700],
        "title_ratio": 0.12,
        "yellow_ratio": 0.03,
        "color_ratio": 0.14,
        "border_score": 0.86,
        "supported_border_sides": 4,
    }
    machine.update(obs(0, inventory_open=True, equipment_slot_hover=True, equipment_tooltip=tooltip))
    events = machine.update(obs(1, inventory_open=True, equipment_slot_hover=True, equipment_tooltip=tooltip))

    equipment_event = next(event for event in events if event.type == "equipment_tooltip_opened")
    assert equipment_event.payload["title_ratio"] == 0.12
    assert equipment_event.payload["color_ratio"] == 0.14
    assert equipment_event.payload["border_score"] == 0.86
    assert equipment_event.payload["supported_border_sides"] == 4


def test_equipment_tooltip_is_blocked_again_after_inventory_closes():
    machine = EventMachine()
    tooltip = {"active": True, "score": 0.91}
    machine.update(obs(0, inventory_open=True, equipment_slot_hover=True, equipment_tooltip=tooltip))
    machine.update(obs(1, inventory_open=True, equipment_slot_hover=True, equipment_tooltip=tooltip))

    machine.update(obs(2, inventory_open=False, equipment_slot_hover=True, equipment_tooltip=tooltip))
    closed = machine.update(obs(3, inventory_open=False, equipment_slot_hover=True, equipment_tooltip=tooltip))
    assert "inventory_closed" in [event.type for event in closed]
    assert machine.state.inventory_open is False

    events = machine.update(obs(20, inventory_open=False, equipment_slot_hover=True, equipment_tooltip=tooltip))
    assert "equipment_tooltip_opened" not in [event.type for event in events]


def test_equipment_tooltip_is_blocked_over_right_item_grid():
    machine = EventMachine()
    tooltip = {"active": True, "score": 0.91}
    machine.update(obs(0, inventory_open=True, equipment_slot_hover=False, equipment_tooltip=tooltip))
    opened = machine.update(obs(1, inventory_open=True, equipment_slot_hover=False, equipment_tooltip=tooltip))

    assert [event.type for event in opened] == ["inventory_opened"]
    assert machine.update(obs(2, inventory_open=True, equipment_slot_hover=False, equipment_tooltip=tooltip)) == []


def fullness_signal(status: str, empty_count: int = 0):
    return {
        "status": status,
        "confidence": 0.96,
        "empty_count": empty_count,
        "occupied_count": 20 - empty_count,
        "unknown_count": 0,
        "reason": "test",
    }


def test_inventory_full_alert_requires_three_consecutive_frames():
    policy = InventoryMonitorConfig(confirm_frames=3, alert_confirm_seconds=0, full_cooldown_seconds=300)
    machine = EventMachine(rules=(), inventory_monitor=policy)

    assert machine.update(obs(0, inventory_fullness=fullness_signal("full"))) == []
    assert machine.update(obs(1, inventory_fullness=fullness_signal("full"))) == []
    events = machine.update(obs(2, inventory_fullness=fullness_signal("full")))

    assert [event.type for event in events] == ["inventory_full"]
    assert events[0].payload["occupied_count"] == 20
    assert machine.update(obs(3, inventory_fullness=fullness_signal("full"))) == []


def test_inventory_full_alert_requires_five_continuous_seconds():
    policy = InventoryMonitorConfig(confirm_frames=3, alert_confirm_seconds=5, full_cooldown_seconds=300)
    machine = EventMachine(rules=(), inventory_monitor=policy)

    for index in range(50):
        assert machine.update(obs(index, inventory_fullness=fullness_signal("full"))) == []
    events = machine.update(obs(50, inventory_fullness=fullness_signal("full")))

    assert [event.type for event in events] == ["inventory_full"]


def test_non_full_frame_breaks_full_confirmation():
    machine = EventMachine(
        rules=(), inventory_monitor=InventoryMonitorConfig(confirm_frames=3, alert_confirm_seconds=0)
    )
    machine.update(obs(0, inventory_fullness=fullness_signal("full")))
    machine.update(obs(1, inventory_fullness=fullness_signal("not_full", 8)))
    machine.update(obs(2, inventory_fullness=fullness_signal("full")))
    assert machine.update(obs(3, inventory_fullness=fullness_signal("full"))) == []

    events = machine.update(obs(4, inventory_fullness=fullness_signal("full")))
    assert [event.type for event in events] == ["inventory_full"]


def test_inventory_closed_reminder_respects_grace_and_repeats_after_cooldown():
    policy = InventoryMonitorConfig(
        confirm_frames=3,
        alert_confirm_seconds=0,
        closed_grace_seconds=1,
        closed_reminder_seconds=1,
    )
    machine = EventMachine(rules=(), inventory_monitor=policy)

    for index in range(10):
        assert machine.update(obs(index, inventory_fullness=fullness_signal("inventory_closed"))) == []
    first = machine.update(obs(10, inventory_fullness=fullness_signal("inventory_closed")))
    assert [event.type for event in first] == ["inventory_not_open"]
    assert machine.update(obs(11, inventory_fullness=fullness_signal("inventory_closed"))) == []
    repeated = machine.update(obs(20, inventory_fullness=fullness_signal("inventory_closed")))
    assert [event.type for event in repeated] == ["inventory_not_open"]


def test_invalid_inventory_emits_blocked_alert_after_confirmation():
    policy = InventoryMonitorConfig(
        confirm_frames=3,
        alert_confirm_seconds=5,
        invalid_reminder_seconds=120,
    )
    machine = EventMachine(rules=(), inventory_monitor=policy)

    for index in range(50):
        assert machine.update(obs(index, inventory_fullness=fullness_signal("invalid"))) == []
    events = machine.update(obs(50, inventory_fullness=fullness_signal("invalid")))

    assert [event.type for event in events] == ["inventory_detection_blocked"]


def test_short_invalid_inventory_hint_does_not_alert_and_resets_timer():
    policy = InventoryMonitorConfig(confirm_frames=3, alert_confirm_seconds=5)
    machine = EventMachine(rules=(), inventory_monitor=policy)

    for index in range(30):
        assert machine.update(obs(index, inventory_fullness=fullness_signal("invalid"))) == []
    assert machine.update(obs(30, inventory_fullness=fullness_signal("not_full", 8))) == []
    for index in range(31, 81):
        assert machine.update(obs(index, inventory_fullness=fullness_signal("invalid"))) == []

    events = machine.update(obs(81, inventory_fullness=fullness_signal("invalid")))
    assert [event.type for event in events] == ["inventory_detection_blocked"]

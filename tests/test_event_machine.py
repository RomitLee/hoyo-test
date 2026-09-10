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

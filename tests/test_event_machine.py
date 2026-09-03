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

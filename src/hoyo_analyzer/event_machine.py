"""Temporal event aggregation and de-duplication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Event, EventStatus, Observation, RuntimeState


@dataclass(frozen=True, slots=True)
class SignalRule:
    signal: str
    event_type: str
    confirm_frames: int = 2
    cooldown_ms: int = 1500
    emit_exit: bool = False
    exit_event_type: str | None = None


DEFAULT_RULES = (
    SignalRule("game_visible", "application_opened"),
    SignalRule("inventory_open", "inventory_opened", exit_event_type="inventory_closed", emit_exit=True),
    SignalRule("battle_active", "battle_started", cooldown_ms=3000, exit_event_type="battle_ended", emit_exit=True),
)


class EventMachine:
    """Convert noisy per-frame signals into confirmed enter/exit events."""

    def __init__(self, rules: tuple[SignalRule, ...] = DEFAULT_RULES, map_cooldown_ms: int = 500) -> None:
        self.rules = rules
        self.state = RuntimeState()
        self._event_counter = 0
        self._trackers: dict[str, dict[str, Any]] = {}
        self._last_event_ms: dict[str, int] = {}
        self.map_cooldown_ms = map_cooldown_ms
        self._last_skill = None
        self._last_damage = None

    def _new_event(self, obs: Observation, kind: str, payload: dict[str, Any] | None = None) -> Event:
        self._event_counter += 1
        return Event(
            f"evt_{self._event_counter:06d}",
            obs.timestamp_ms,
            kind,
            confidence=obs.confidence,
            payload=payload or {},
            evidence_frame_index=obs.frame_index,
        )

    def _transition(self, rule: SignalRule, active: bool) -> bool:
        tracker = self._trackers.setdefault(rule.signal, {"candidate": None, "count": 0, "stable": False})
        if tracker["candidate"] == active:
            tracker["count"] += 1
        else:
            tracker.update(candidate=active, count=1)
        if tracker["stable"] == active or tracker["count"] < rule.confirm_frames:
            return False
        tracker["stable"] = active
        return True

    def _emit_if_allowed(self, event: Event, cooldown_ms: int) -> Event | None:
        last = self._last_event_ms.get(event.type)
        if last is not None and event.timestamp_ms - last < cooldown_ms:
            return None
        self._last_event_ms[event.type] = event.timestamp_ms
        return event

    def update(self, obs: Observation) -> list[Event]:
        events: list[Event] = []
        for rule in self.rules:
            raw = obs.get(rule.signal, False)
            active = bool(raw.get("active", False) if isinstance(raw, dict) else raw)
            if not self._transition(rule, active):
                continue
            if active:
                event = self._emit_if_allowed(self._new_event(obs, rule.event_type), rule.cooldown_ms)
            elif rule.emit_exit and rule.exit_event_type:
                event = self._emit_if_allowed(self._new_event(obs, rule.exit_event_type), rule.cooldown_ms)
            else:
                event = None
            if event:
                events.append(event)
        map_name = obs.get("map_name")
        if map_name and map_name != self.state.map_name:
            event = self._emit_if_allowed(
                self._new_event(obs, "map_entered", {"map_name": str(map_name)}), self.map_cooldown_ms
            )
            if event:
                events.append(event)
            self.state.map_name = str(map_name)
        items = obs.get("inventory_items")
        if items is not None:
            normalized = tuple(str(item) for item in items)
            if normalized != self.state.inventory_items:
                self.state.inventory_items = normalized
                event = self._new_event(obs, "inventory_items_read", {"items": list(normalized)})
                event.status = EventStatus.UPDATED
                events.append(event)
        skill = obs.get("skill_name")
        if skill and str(skill) != self._last_skill:
            self._last_skill = str(skill)
            events.append(self._new_event(obs, "skill_used", {"skill_name": self._last_skill}))
        damage = obs.get("damage_amount")
        if damage is not None:
            try:
                amount = int(damage)
            except (TypeError, ValueError):
                amount = None
            if amount is not None and amount != self._last_damage:
                self._last_damage = amount
                events.append(self._new_event(obs, "damage_dealt", {"amount": amount, "skill_name": self._last_skill}))
        self.state.game_visible = bool(obs.get("game_visible", self.state.game_visible))
        self.state.inventory_open = bool(obs.get("inventory_open", self.state.inventory_open))
        self.state.battle_active = bool(obs.get("battle_active", self.state.battle_active))
        self.state.skill_name = self._last_skill
        self.state.damage_amount = self._last_damage
        return events

    def close(self, obs: Observation) -> list[Event]:
        events: list[Event] = []
        for rule in self.rules:
            tracker = self._trackers.get(rule.signal, {})
            if rule.emit_exit and rule.exit_event_type and tracker.get("stable"):
                events.append(self._new_event(obs, rule.exit_event_type))
                tracker["stable"] = False
        self.state.inventory_open = False
        self.state.battle_active = False
        return events

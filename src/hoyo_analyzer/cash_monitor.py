"""Emit confirmed cash changes and explicit continuity boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4


class CashMonitor:
    def __init__(self, identity: dict[str, str]) -> None:
        self.identity = identity
        self.stream: str | None = None
        self.sequence = 0
        self.last_cash: int | None = None
        self.last_time: datetime | None = None
        self.boundary: datetime | None = None

    def _payload(self, when: datetime, cash: int | None, active: bool) -> dict[str, Any]:
        self.sequence += 1
        return {**self.identity, "event_id": uuid4().hex, "stream_id": self.stream,
                "sequence": self.sequence, "occurred_at": when.isoformat(timespec="milliseconds"),
                "cash": cash, "active": active}

    def stop(self, when: datetime) -> list[dict[str, Any]]:
        if self.boundary is None or when > self.boundary:
            self.boundary = when
        if self.stream is None:
            return []
        result = self._payload(when, None, False)
        self.stream, self.last_time, self.last_cash = None, None, None
        return [result]

    def update(self, signal: dict[str, Any], enabled: bool, when: datetime) -> list[dict[str, Any]]:
        result = []
        # An undecodable frame is a gap; a still-valid candidate waiting for
        # its third frame is not a gap and must not lose the preceding delta.
        if not enabled or not signal.get("cash_detected"):
            return self.stop(when)
        if self.boundary is not None and when <= self.boundary:
            return []  # A queued frame from before the interruption is stale.
        if self.last_time is not None and (when - self.last_time).total_seconds() > 15:
            result.extend(self.stop(when - timedelta(milliseconds=1)))
        if not signal.get("cash_readable") or signal.get("cash") is None:
            return result
        cash = int(signal["cash"])
        if self.stream is None:
            self.stream, self.sequence = uuid4().hex, 0
        elif (self.last_cash == cash and self.last_time is not None
              and (when - self.last_time).total_seconds() < 5
              and when.strftime("%Y-%m-%d-%H") == self.last_time.strftime("%Y-%m-%d-%H")):
            # Heartbeats provide coverage for constant balances; hour changes
            # force a new observation even within the five-second interval.
            return result
        result.append(self._payload(when, cash, True))
        self.last_cash, self.last_time = cash, when
        return result

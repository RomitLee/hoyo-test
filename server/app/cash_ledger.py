"""Account-isolated cash observations and Beijing natural-hour accounting."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

BEIJING = timezone(timedelta(hours=8))
MAX_GAP_SECONDS = 15


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS cash_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
            device_id TEXT NOT NULL, event_id TEXT NOT NULL, character_id TEXT NOT NULL,
            stream_id TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at TEXT NOT NULL,
            cash INTEGER, active INTEGER NOT NULL, disposition TEXT NOT NULL,
            UNIQUE(device_id,event_id), UNIQUE(device_id,stream_id,sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_cash_observations_character
            ON cash_observations(account_id,character_id,occurred_at);
        CREATE TABLE IF NOT EXISTS cash_states (
            account_id INTEGER NOT NULL, character_id TEXT NOT NULL, device_id TEXT NOT NULL,
            stream_id TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at TEXT NOT NULL,
            cash INTEGER, active INTEGER NOT NULL,
            PRIMARY KEY(account_id,character_id)
        );
        CREATE TABLE IF NOT EXISTS cash_hours (
            account_id INTEGER NOT NULL, character_id TEXT NOT NULL, day TEXT NOT NULL,
            hour INTEGER NOT NULL, server_name TEXT, role_name TEXT,
            opening_cash INTEGER NOT NULL, closing_cash INTEGER NOT NULL,
            topups INTEGER NOT NULL DEFAULT 0, purchases INTEGER NOT NULL DEFAULT 0,
            restarts INTEGER NOT NULL DEFAULT 0, monitored_seconds REAL NOT NULL DEFAULT 0,
            first_at TEXT NOT NULL, last_at TEXT NOT NULL,
            PRIMARY KEY(account_id,character_id,day,hour)
        );
    """)


def record(connection: sqlite3.Connection, account_id: int, device_id: str, payload: dict[str, Any]) -> str:
    character_id = payload["character_id"]
    observed = datetime.fromisoformat(payload["occurred_at"]).astimezone(UTC)
    timestamp = observed.isoformat(timespec="milliseconds")
    previous = connection.execute("SELECT * FROM cash_states WHERE account_id=? AND character_id=?",
                                  (account_id, character_id)).fetchone()
    active, cash = bool(payload["active"]), payload.get("cash")
    stream, sequence = payload["stream_id"], payload["sequence"]
    disposition = "recorded"
    gap = None if previous is None else (observed - datetime.fromisoformat(previous["occurred_at"])).total_seconds()
    if previous is not None:
        if gap <= 0 or (previous["stream_id"] == stream and previous["device_id"] == device_id
                        and sequence <= previous["sequence"]):
            disposition = "out_of_order"
        elif (previous["active"] and gap <= MAX_GAP_SECONDS
              and (previous["device_id"] != device_id or previous["stream_id"] != stream)):
            disposition = "source_conflict"
        elif not active and (previous["stream_id"] != stream or previous["device_id"] != device_id):
            disposition = "obsolete_end"
    inserted = connection.execute(
        """INSERT OR IGNORE INTO cash_observations(account_id,device_id,event_id,character_id,stream_id,
           sequence,occurred_at,cash,active,disposition) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (account_id, device_id, payload["event_id"], character_id, stream, sequence,
         timestamp, cash, int(active), disposition),
    )
    if not inserted.rowcount:
        return "duplicate"
    if disposition != "recorded":
        return disposition
    continuous = bool(active and previous is not None and previous["active"]
                      and previous["stream_id"] == stream and previous["device_id"] == device_id
                      and 0 < gap <= MAX_GAP_SECONDS)
    if active:
        local = observed.astimezone(BEIJING)
        day, hour = local.date().isoformat(), local.hour
        opening = previous["cash"] if continuous else cash
        connection.execute(
            """INSERT OR IGNORE INTO cash_hours(account_id,character_id,day,hour,server_name,role_name,
               opening_cash,closing_cash,first_at,last_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (account_id, character_id, day, hour, payload.get("server_name"), payload.get("role_name"),
             opening, opening, timestamp, timestamp),
        )
        delta = cash - previous["cash"] if continuous else 0
        connection.execute(
            """UPDATE cash_hours SET closing_cash=?,topups=topups+?,purchases=purchases+?,
               restarts=restarts+?,last_at=?,server_name=?,role_name=?
               WHERE account_id=? AND character_id=? AND day=? AND hour=?""",
            (cash, max(0, delta), max(0, -delta), int(previous is not None and not continuous),
             timestamp, payload.get("server_name"), payload.get("role_name"), account_id, character_id, day, hour),
        )
        if continuous:
            # Split observed coverage at hour/day boundaries. Never fill an
            # offline hour with a zero-spend row.
            start = datetime.fromisoformat(previous["occurred_at"]).astimezone(BEIJING)
            while start < local:
                end = min(local, start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                connection.execute(
                    """UPDATE cash_hours SET monitored_seconds=monitored_seconds+?
                       WHERE account_id=? AND character_id=? AND day=? AND hour=?""",
                    ((end - start).total_seconds(), account_id, character_id, start.date().isoformat(), start.hour),
                )
                start = end
    connection.execute(
        """INSERT INTO cash_states(account_id,character_id,device_id,stream_id,sequence,occurred_at,cash,active)
           VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(account_id,character_id) DO UPDATE SET
           device_id=excluded.device_id,stream_id=excluded.stream_id,sequence=excluded.sequence,
           occurred_at=excluded.occurred_at,cash=COALESCE(excluded.cash,cash_states.cash),active=excluded.active""",
        (account_id, character_id, device_id, stream, sequence, timestamp, cash, int(active)),
    )
    return disposition


def statistics(connection: sqlite3.Connection, account_id: int, day: str) -> dict[str, Any]:
    hours = [dict(row) for row in connection.execute(
        "SELECT * FROM cash_hours WHERE account_id=? AND day=? ORDER BY character_id,hour", (account_id, day)
    )]
    daily: dict[str, dict[str, Any]] = {}
    for row in hours:
        row.pop("account_id")
        row["unobserved_change"] = row["closing_cash"] - row["opening_cash"] - row["topups"] + row["purchases"]
        key = row["character_id"]
        if key not in daily:
            daily[key] = {**row, "hours": 0, "topups": 0, "purchases": 0, "monitored_seconds": 0, "restarts": 0}
            daily[key].pop("hour")
        total = daily[key]
        total.update(closing_cash=row["closing_cash"], last_at=row["last_at"],
                     server_name=row["server_name"], role_name=row["role_name"])
        total["hours"] += 1
        for name in ("topups", "purchases", "monitored_seconds", "restarts"):
            total[name] += row[name]
        total["unobserved_change"] = total["closing_cash"] - total["opening_cash"] - total["topups"] + total["purchases"]
    return {"day": day, "timezone": "Asia/Shanghai", "daily": list(daily.values()), "hourly": hours,
            "attribution": "金额变化按连续监控中确认的北京时间归属，仅反映观测净变化"}

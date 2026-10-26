"""Complete a dialog's opening log without counting OCR confirmation as a change."""

from __future__ import annotations

import sqlite3
from typing import Any

BOUNDARY_TYPES = "'panel_opened','panel_closed','game_default','panel_target_changed','monitoring_started','monitoring_paused','window_minimized'"
POSITION = "julianday(COALESCE(target_confirmed_at,occurred_at))"


def target_message(payload: dict[str, Any], *, opened: bool) -> str:
    panel = "给予" if payload.get("panel_type") == "give" else "交易"
    prefix = f"打开{panel}面板，" if opened else f"{panel}"
    if payload.get("target_status") == "recognized":
        return f"{prefix}对象：{payload['target_name']}，{payload['target_level']}级"
    return f"{prefix}对象未识别"


def remember_confirmation(
    connection: sqlite3.Connection, account_id: int, device_id: str,
    payload: dict[str, Any], received_at: str, log_id: int | None,
) -> None:
    connection.execute(
        """INSERT INTO monitor_target_receipts
           (device_id,client_event_id,account_id,log_id,occurred_at,received_at) VALUES (?,?,?,?,?,?)""",
        (device_id, payload["event_id"], account_id, log_id, str(payload["occurred_at"]), received_at),
    )


def merge_confirmation(
    connection: sqlite3.Connection, account_id: int, device_id: str,
    payload: dict[str, Any], received_at: str,
) -> bool:
    """Return True when this report was consumed; caller holds a write transaction."""
    if payload["event_type"] != "panel_target_changed":
        return False
    event_key = device_id, payload["event_id"]
    if connection.execute(
        "SELECT 1 FROM monitor_target_receipts WHERE device_id=? AND client_event_id=?", event_key,
    ).fetchone() or connection.execute(
        "SELECT 1 FROM monitor_logs WHERE device_id=? AND client_event_id=?", event_key,
    ).fetchone():
        return True

    # Loss of readability says nothing about the historical recipient. Keep
    # the last confirmed identity so recovery to the same player is not a change.
    if payload.get("target_status") != "recognized":
        remember_confirmation(connection, account_id, device_id, payload, received_at, None)
        return True
    if not payload.get("window_id"):
        return False

    # Compare event times in UTC, not their textual offsets or receipt order.
    # A device-wide pause/start is a boundary for every monitored window.
    common = f"""FROM monitor_logs WHERE account_id=? AND device_id=?
                 AND event_type IN ({BOUNDARY_TYPES})
                 AND (event_type!='panel_target_changed' OR target_status='recognized')
                 AND {POSITION}<=julianday(?)"""
    parameters = account_id, device_id, str(payload["occurred_at"])
    order = f" ORDER BY {POSITION} DESC,id DESC LIMIT 1"
    rows = [connection.execute(
        f"SELECT *,{POSITION} AS position {common} AND window_id=?" + order,
        (*parameters, payload["window_id"]),
    ).fetchone(), connection.execute(
        f"""SELECT *,{POSITION} AS position {common} AND window_id IS NULL
        AND event_type IN ('monitoring_started','monitoring_paused')""" + order, parameters,
    ).fetchone()]
    previous = max((row for row in rows if row is not None), key=lambda row: (row["position"], row["id"]), default=None)
    if previous is None or previous["event_type"] not in {"panel_opened", "panel_target_changed"}:
        return False
    if previous["panel_type"] != payload.get("panel_type"):
        return False
    if any(previous[key] != payload.get(key) for key in ("character_id", "server_name", "role_name")):
        return False

    known = previous["target_status"] == "recognized" and previous["target_name"] and previous["target_level"]
    same = previous["target_name"] == payload["target_name"] and previous["target_level"] == payload["target_level"]
    if known and not same:
        return False  # A real A -> B (including B -> A) change gets its own row.
    if not known and previous["event_type"] != "panel_opened":
        return False

    if not known:
        connection.execute(
            """UPDATE monitor_logs SET target_name=?,target_level=?,target_confidence=?,
               target_status='recognized',target_confirmed_at=?,message=?
               WHERE id=? AND account_id=? AND device_id=?""",
            (payload["target_name"], payload["target_level"], payload.get("target_confidence"),
             str(payload["occurred_at"]), target_message(payload, opened=True),
             previous["id"], account_id, device_id),
        )
    remember_confirmation(connection, account_id, device_id, payload, received_at, previous["id"])
    return True

"""Small SQLite persistence layer with durable acknowledgement semantics."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RelayDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = FULL;

                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    hostname TEXT,
                    app_version TEXT,
                    ip_address TEXT,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    client_event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    server_name TEXT,
                    role_name TEXT,
                    character_id TEXT,
                    empty_slots INTEGER,
                    confidence REAL,
                    occlusion_percent REAL,
                    minimized INTEGER,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    acked_at TEXT,
                    UNIQUE(device_id, client_event_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_pending
                ON events(acked_at, id);

                CREATE TABLE IF NOT EXISTS device_window_status (
                    device_id TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    title TEXT,
                    server_name TEXT,
                    role_name TEXT,
                    character_id TEXT,
                    status TEXT NOT NULL,
                    status_text TEXT,
                    empty_slots INTEGER,
                    occupied_slots INTEGER,
                    confidence REAL,
                    occlusion_percent REAL,
                    minimized INTEGER NOT NULL DEFAULT 0,
                    monitoring INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY(device_id, window_id),
                    FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_window_status_device
                ON device_window_status(device_id, updated_at);
                """
            )

    def upsert_device(
        self,
        device_id: str,
        *,
        hostname: str | None,
        app_version: str | None,
        ip_address: str | None,
    ) -> str:
        seen_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO devices(device_id, hostname, app_version, ip_address, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    hostname = COALESCE(excluded.hostname, devices.hostname),
                    app_version = COALESCE(excluded.app_version, devices.app_version),
                    ip_address = excluded.ip_address,
                    last_seen_at = excluded.last_seen_at
                """,
                (device_id, hostname, app_version, ip_address, seen_at),
            )
        return seen_at

    def insert_event(self, device_id: str, payload: dict[str, Any]) -> tuple[int, bool, str]:
        received_at = utc_now()
        values = (
            device_id,
            payload["event_id"],
            payload["event_type"],
            payload.get("server_name"),
            payload.get("role_name"),
            payload.get("character_id"),
            payload.get("empty_slots"),
            payload.get("confidence"),
            payload.get("occlusion_percent"),
            None if payload.get("minimized") is None else int(payload["minimized"]),
            payload["occurred_at"],
            received_at,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO events(
                        device_id, client_event_id, event_type, server_name, role_name,
                        character_id, empty_slots, confidence, occlusion_percent,
                        minimized, occurred_at, received_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                return int(cursor.lastrowid), False, received_at
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT id, received_at FROM events
                    WHERE device_id = ? AND client_event_id = ?
                    """,
                    (device_id, payload["event_id"]),
                ).fetchone()
                if row is None:
                    raise
                return int(row["id"]), True, str(row["received_at"])

    def list_events(self, *, pending_only: bool, after_id: int, limit: int) -> list[dict[str, Any]]:
        predicates = ["id > ?"]
        parameters: list[Any] = [after_id]
        if pending_only:
            predicates.append("acked_at IS NULL")
        parameters.append(limit)
        query = f"""
            SELECT id, device_id, client_event_id, event_type, server_name, role_name,
                   character_id, empty_slots, confidence, occlusion_percent, minimized,
                   occurred_at, received_at, payload_json, acked_at
            FROM events
            WHERE {' AND '.join(predicates)}
            ORDER BY id ASC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._event_row(row) for row in rows]

    def acknowledge_event(self, event_id: int) -> tuple[bool, str | None]:
        acknowledged_at = utc_now()
        with self._connect() as connection:
            row = connection.execute("SELECT acked_at FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                return False, None
            existing = row["acked_at"]
            if existing is not None:
                return True, str(existing)
            connection.execute("UPDATE events SET acked_at = ? WHERE id = ?", (acknowledged_at, event_id))
        return True, acknowledged_at


    def replace_window_status(self, device_id: str, windows: list[dict[str, Any]]) -> str:
        """Replace the latest status snapshot for one slave device."""

        received_at = utc_now()
        with self._connect() as connection:
            connection.execute("DELETE FROM device_window_status WHERE device_id = ?", (device_id,))
            connection.executemany(
                """
                INSERT INTO device_window_status(
                    device_id, window_id, title, server_name, role_name, character_id,
                    status, status_text, empty_slots, occupied_slots, confidence,
                    occlusion_percent, minimized, monitoring, updated_at, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        device_id,
                        str(window.get("window_id", "")),
                        window.get("title"),
                        window.get("server_name"),
                        window.get("role_name"),
                        window.get("character_id"),
                        str(window.get("status") or "unknown"),
                        window.get("status_text"),
                        window.get("empty_slots"),
                        window.get("occupied_slots"),
                        window.get("confidence"),
                        window.get("occlusion_percent"),
                        int(bool(window.get("minimized"))),
                        int(bool(window.get("monitoring", True))),
                        str(window.get("updated_at") or received_at),
                        received_at,
                    )
                    for window in windows
                    if str(window.get("window_id", ""))
                ],
            )
        return received_at

    def list_device_status(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            devices = connection.execute(
                """
                SELECT device_id, hostname, app_version, ip_address, last_seen_at
                FROM devices ORDER BY device_id ASC
                """
            ).fetchall()
            windows = connection.execute(
                """
                SELECT device_id, window_id, title, server_name, role_name, character_id,
                       status, status_text, empty_slots, occupied_slots, confidence,
                       occlusion_percent, minimized, monitoring, updated_at, received_at
                FROM device_window_status
                ORDER BY device_id ASC, window_id ASC
                """
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in windows:
            item = dict(row)
            item["minimized"] = bool(item["minimized"])
            item["monitoring"] = bool(item["monitoring"])
            grouped.setdefault(str(item.pop("device_id")), []).append(item)
        result = []
        for row in devices:
            item = dict(row)
            item["windows"] = grouped.get(str(item["device_id"]), [])
            result.append(item)
        return result

    def list_devices(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT device_id, hostname, app_version, ip_address, last_seen_at
                FROM devices ORDER BY device_id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_acknowledged_events(self, retention_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(timespec="milliseconds")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM events WHERE acked_at IS NOT NULL AND acked_at < ?",
                (cutoff,),
            )
            return max(0, cursor.rowcount)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["event_id"] = item.pop("client_event_id")
        item["server_event_id"] = item.pop("id")
        if item["minimized"] is not None:
            item["minimized"] = bool(item["minimized"])
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

"""SQLite persistence for accounts, pairing, devices, status and alarms."""

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
                CREATE TABLE IF NOT EXISTS monitor_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL, device_id TEXT NOT NULL,
                    client_event_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    window_id TEXT, server_name TEXT, role_name TEXT, character_id TEXT,
                    occlusion_percent REAL, minimized INTEGER,
                    occurred_at TEXT NOT NULL, received_at TEXT NOT NULL, reason TEXT,
                    UNIQUE(device_id, client_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_logs_account
                ON monitor_logs(account_id, id DESC);
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE, display_name TEXT, password_hash TEXT,
                    created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
                    target_device_id TEXT, created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, used_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY, hostname TEXT, app_version TEXT,
                    ip_address TEXT, last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
                    client_event_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    server_name TEXT, role_name TEXT, character_id TEXT,
                    empty_slots INTEGER, confidence REAL, occlusion_percent REAL,
                    minimized INTEGER, occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                    acked_at TEXT, UNIQUE(device_id, client_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_pending ON events(acked_at, id);
                CREATE TABLE IF NOT EXISTS device_window_status (
                    device_id TEXT NOT NULL, window_id TEXT NOT NULL, title TEXT,
                    server_name TEXT, role_name TEXT, character_id TEXT,
                    status TEXT NOT NULL, status_text TEXT, empty_slots INTEGER,
                    occupied_slots INTEGER, confidence REAL, occlusion_percent REAL,
                    minimized INTEGER NOT NULL DEFAULT 0,
                    monitoring INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
                    received_at TEXT NOT NULL, PRIMARY KEY(device_id, window_id),
                    FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_window_status_device
                ON device_window_status(device_id, updated_at);
                CREATE TABLE IF NOT EXISTS bark_settings (
                    account_id INTEGER PRIMARY KEY, device_key TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0, call_enabled INTEGER NOT NULL DEFAULT 1,
                    repeat_minutes INTEGER NOT NULL DEFAULT 10, notify_after TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(connection, "devices", "account_id", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "devices", "display_name", "TEXT")
            self._ensure_column(connection, "devices", "note", "TEXT")
            self._ensure_column(connection, "devices", "credential_hash", "TEXT")
            self._ensure_column(connection, "devices", "installation_id", "TEXT")
            self._ensure_column(connection, "devices", "created_at", "TEXT")
            self._ensure_column(connection, "devices", "disabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "devices", "removed_at", "TEXT")
            self._ensure_column(connection, "events", "account_id", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "events", "bark_last_sent_at", "TEXT")
            self._ensure_column(connection, "events", "bark_send_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "bark_settings", "notify_after", "TEXT")
            self._ensure_column(connection, "monitor_logs", "panel_type", "TEXT")
            self._ensure_column(connection, "monitor_logs", "confidence", "REAL")
            self._ensure_column(connection, "monitor_logs", "message", "TEXT")
            for name, kind in (("cash", "INTEGER"), ("bank_money", "INTEGER"), ("money_confirmed_at", "TEXT"), ("money_readable", "INTEGER NOT NULL DEFAULT 0")):
                self._ensure_column(connection, "device_window_status", name, kind)
            now = utc_now()
            connection.execute(
                "INSERT OR IGNORE INTO accounts(id, username, display_name, password_hash, created_at, disabled) VALUES (1, NULL, '原有主机', NULL, ?, 0)",
                (now,),
            )
            connection.execute("UPDATE devices SET created_at=COALESCE(created_at,last_seen_at,?)", (now,))

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_account(self, username: str, display_name: str, password_hash: str) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM accounts WHERE lower(username)=lower(?)", (username,)
            ).fetchone():
                raise ValueError("账号名已存在")
            legacy = connection.execute("SELECT id FROM accounts WHERE id=1 AND username IS NULL").fetchone()
            if legacy:
                account_id = 1
                connection.execute(
                    "UPDATE accounts SET username=?,display_name=?,password_hash=? WHERE id=1",
                    (username, display_name, password_hash),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO accounts(username,display_name,password_hash,created_at) VALUES (?,?,?,?)",
                    (username, display_name, password_hash, now),
                )
                account_id = int(cursor.lastrowid)
        return {"account_id": account_id, "username": username, "display_name": display_name}

    def account_by_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,username,display_name,password_hash,disabled FROM accounts WHERE lower(username)=lower(?)",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def account_by_id(self, account_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,username,display_name,disabled FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_session(self, account_id: int, token_hash: str, expires_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(token_hash,account_id,created_at,expires_at) VALUES (?,?,?,?)",
                (token_hash, account_id, utc_now(), expires_at),
            )

    def authenticate_session(self, token_hash: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.account_id FROM sessions s JOIN accounts a ON a.id=s.account_id
                   WHERE s.token_hash=? AND s.expires_at>? AND a.disabled=0""",
                (token_hash, utc_now()),
            ).fetchone()
        return int(row["account_id"]) if row else None

    def create_pairing_code(
        self, account_id: int, code_hash: str, expires_at: str, target_device_id: str | None = None
    ) -> None:
        with self._connect() as connection:
            if target_device_id:
                row = connection.execute(
                    "SELECT 1 FROM devices WHERE device_id=? AND account_id=? AND removed_at IS NULL",
                    (target_device_id, account_id),
                ).fetchone()
                if not row:
                    raise LookupError("从机不存在")
                connection.execute("UPDATE devices SET credential_hash=NULL WHERE device_id=?", (target_device_id,))
            connection.execute(
                "INSERT INTO pairing_codes(code_hash,account_id,target_device_id,created_at,expires_at) VALUES (?,?,?,?,?)",
                (code_hash, account_id, target_device_id, utc_now(), expires_at),
            )

    def consume_pairing_code(
        self, code_hash: str, *, credential_hash: str, installation_id: str,
        hostname: str | None, app_version: str | None,
    ) -> tuple[str, int] | None:
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT account_id,target_device_id FROM pairing_codes WHERE code_hash=? AND used_at IS NULL AND expires_at>?",
                (code_hash, now),
            ).fetchone()
            if not row:
                return None
            account_id = int(row["account_id"])
            target = row["target_device_id"]
            if target:
                device_id = str(target)
                connection.execute(
                    """UPDATE devices SET credential_hash=?,installation_id=?,hostname=COALESCE(?,hostname),
                       app_version=COALESCE(?,app_version),disabled=0,removed_at=NULL,last_seen_at=?
                       WHERE device_id=? AND account_id=?""",
                    (credential_hash, installation_id, hostname, app_version, now, device_id, account_id),
                )
            else:
                existing = connection.execute(
                    "SELECT device_id FROM devices WHERE installation_id=? AND account_id=? AND removed_at IS NULL",
                    (installation_id, account_id),
                ).fetchone()
                device_id = str(existing["device_id"]) if existing else self._new_device_id(connection, account_id)
                if existing:
                    connection.execute(
                        "UPDATE devices SET credential_hash=?,hostname=COALESCE(?,hostname),app_version=COALESCE(?,app_version),disabled=0,last_seen_at=? WHERE device_id=?",
                        (credential_hash, hostname, app_version, now, device_id),
                    )
                else:
                    connection.execute(
                        """INSERT INTO devices(device_id,account_id,display_name,hostname,app_version,last_seen_at,
                           credential_hash,installation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (device_id, account_id, hostname or device_id, hostname, app_version, now,
                         credential_hash, installation_id, now),
                    )
            connection.execute("UPDATE pairing_codes SET used_at=? WHERE code_hash=?", (now, code_hash))
        return device_id, account_id

    @staticmethod
    def _new_device_id(connection: sqlite3.Connection, account_id: int) -> str:
        number = int(connection.execute(
            "SELECT COUNT(*)+1 FROM devices WHERE account_id=?", (account_id,)
        ).fetchone()[0])
        while True:
            candidate = f"slave-{account_id:03d}-{number:03d}"
            if not connection.execute("SELECT 1 FROM devices WHERE device_id=?", (candidate,)).fetchone():
                return candidate
            number += 1

    def authenticate_device(self, device_id: str, credential_hash: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT account_id FROM devices WHERE device_id=? AND credential_hash=? AND disabled=0 AND removed_at IS NULL",
                (device_id, credential_hash),
            ).fetchone()
        return int(row["account_id"]) if row else None

    def register_legacy_device(self, device_id: str, credential_hash: str) -> int:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO devices(device_id,account_id,display_name,last_seen_at,created_at,credential_hash)
                   VALUES (?,1,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET credential_hash=excluded.credential_hash""",
                (device_id, device_id, now, now, credential_hash),
            )
        return 1

    def update_device(self, account_id: int, device_id: str, changes: dict[str, Any]) -> bool:
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key in {"display_name", "note", "disabled"}:
                assignments.append(f"{key}=?")
                values.append(int(value) if key == "disabled" else value)
        if not assignments:
            return False
        values.extend([device_id, account_id])
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE devices SET {', '.join(assignments)} WHERE device_id=? AND account_id=? AND removed_at IS NULL",
                values,
            )
            return cursor.rowcount > 0

    def remove_device(self, account_id: int, device_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE devices SET removed_at=?,disabled=1,credential_hash=NULL WHERE device_id=? AND account_id=? AND removed_at IS NULL",
                (utc_now(), device_id, account_id),
            )
            if cursor.rowcount:
                connection.execute("DELETE FROM device_window_status WHERE device_id=?", (device_id,))
            return cursor.rowcount > 0

    def upsert_device(
        self, device_id: str, *, hostname: str | None, app_version: str | None, ip_address: str | None
    ) -> str:
        seen_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE devices SET hostname=COALESCE(?,hostname),app_version=COALESCE(?,app_version),ip_address=?,last_seen_at=? WHERE device_id=?",
                (hostname, app_version, ip_address, seen_at, device_id),
            )
        return seen_at

    def insert_event(self, account_id: int, device_id: str, payload: dict[str, Any]) -> tuple[int, bool, str]:
        received_at = utc_now()
        values = (
            account_id, device_id, payload["event_id"], payload["event_type"], payload.get("server_name"),
            payload.get("role_name"), payload.get("character_id"), payload.get("empty_slots"),
            payload.get("confidence"), payload.get("occlusion_percent"),
            None if payload.get("minimized") is None else int(payload["minimized"]),
            payload["occurred_at"], received_at,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO events(account_id,device_id,client_event_id,event_type,server_name,role_name,
                       character_id,empty_slots,confidence,occlusion_percent,minimized,occurred_at,received_at,payload_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                return int(cursor.lastrowid), False, received_at
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT id,received_at FROM events WHERE device_id=? AND client_event_id=?",
                    (device_id, payload["event_id"]),
                ).fetchone()
                if row is None:
                    raise
                return int(row["id"]), True, str(row["received_at"])

    def replace_window_status(
        self, device_id: str, windows: list[dict[str, Any]], reported_at: str | None = None,
    ) -> str:
        received_at = utc_now()
        with self._connect() as connection:
            device = connection.execute("SELECT account_id FROM devices WHERE device_id=?", (device_id,)).fetchone()
            for window in windows:
                for kind, active in (
                    ("window_occluded", float(window.get("occlusion_percent") or 0) > 0),
                    ("window_minimized", bool(window.get("minimized"))),
                ):
                    if active and device:
                        occurred = str(window.get("updated_at") or reported_at or received_at)
                        self._insert_monitor_log(connection, int(device["account_id"]), device_id, {
                            **window, "event_type": kind, "occurred_at": occurred,
                            "event_id": f"status:{reported_at or occurred}:{window['window_id']}:{kind}",
                        }, received_at)
            connection.execute("DELETE FROM device_window_status WHERE device_id=?", (device_id,))
            connection.executemany(
                """INSERT INTO device_window_status(device_id,window_id,title,server_name,role_name,character_id,
                   status,status_text,empty_slots,occupied_slots,confidence,occlusion_percent,minimized,monitoring,
                   updated_at,received_at,cash,bank_money,money_confirmed_at,money_readable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(
                    device_id, str(w.get("window_id", "")), w.get("title"), w.get("server_name"),
                    w.get("role_name"), w.get("character_id"), str(w.get("status") or "unknown"),
                    w.get("status_text"), w.get("empty_slots"), w.get("occupied_slots"), w.get("confidence"),
                    w.get("occlusion_percent"), int(bool(w.get("minimized"))), int(bool(w.get("monitoring", True))),
                    str(w.get("updated_at") or received_at), received_at,
                    w.get("cash"), w.get("bank_money"), w.get("money_confirmed_at"), int(bool(w.get("money_readable"))),
                ) for w in windows if str(w.get("window_id", ""))],
            )
        return received_at

    @staticmethod
    def _insert_monitor_log(
        connection: sqlite3.Connection, account_id: int, device_id: str,
        payload: dict[str, Any], received_at: str,
    ) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO monitor_logs(account_id,device_id,client_event_id,event_type,
               window_id,server_name,role_name,character_id,occlusion_percent,minimized,
               occurred_at,received_at,reason,panel_type,confidence,message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, device_id, payload["event_id"], payload["event_type"], payload.get("window_id"),
             payload.get("server_name"), payload.get("role_name"), payload.get("character_id"),
             payload.get("occlusion_percent"),
             None if payload.get("minimized") is None else int(payload["minimized"]),
             str(payload["occurred_at"]), received_at, payload.get("reason"), payload.get("panel_type"),
             payload.get("confidence"), payload.get("message")),
        )

    def insert_monitor_log(self, account_id: int, device_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            self._insert_monitor_log(connection, account_id, device_id, payload, utc_now())

    def list_monitor_logs(
        self, account_id: int, *, before_id: int = 0, limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT l.*,d.display_name,d.hostname FROM monitor_logs l
                   LEFT JOIN devices d ON d.device_id=l.device_id
                   WHERE l.account_id=? AND (?=0 OR l.id<?) ORDER BY l.id DESC LIMIT ?""",
                (account_id, before_id, before_id, limit),
            ).fetchall()
        return [{**dict(row), "minimized": None if row["minimized"] is None else bool(row["minimized"])}
                for row in rows]

    def list_events(self, account_id: int, *, pending_only: bool, after_id: int, limit: int) -> list[dict[str, Any]]:
        predicates = ["account_id=?", "id>?"]
        parameters: list[Any] = [account_id, after_id]
        if pending_only:
            predicates.append("acked_at IS NULL")
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT id,device_id,client_event_id,event_type,server_name,role_name,character_id,
                   empty_slots,confidence,occlusion_percent,minimized,occurred_at,received_at,payload_json,acked_at
                   FROM events WHERE {' AND '.join(predicates)} ORDER BY id ASC LIMIT ?""",
                parameters,
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def acknowledge_event(self, account_id: int, event_id: int) -> tuple[bool, str | None]:
        acknowledged_at = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT acked_at FROM events WHERE id=? AND account_id=?", (event_id, account_id)
            ).fetchone()
            if row is None:
                return False, None
            if row["acked_at"] is not None:
                return True, str(row["acked_at"])
            connection.execute("UPDATE events SET acked_at=? WHERE id=?", (acknowledged_at, event_id))
        return True, acknowledged_at

    def get_bark_settings(self, account_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT device_key,enabled,call_enabled,repeat_minutes,notify_after,updated_at FROM bark_settings WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            return {
                "device_key": "", "enabled": False, "call_enabled": True,
                "repeat_minutes": 10, "notify_after": None, "updated_at": None,
            }
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["call_enabled"] = bool(item["call_enabled"])
        return item

    def update_bark_settings(
        self, account_id: int, *, device_key: str | None, enabled: bool,
        call_enabled: bool, repeat_minutes: int,
    ) -> dict[str, Any]:
        current = self.get_bark_settings(account_id)
        stored_key = current["device_key"] if device_key is None else device_key
        if enabled and not stored_key:
            raise ValueError("请先填写 Bark 设备密钥")
        now = utc_now()
        notify_after = current["notify_after"]
        if enabled and not current["enabled"]:
            notify_after = now
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO bark_settings(account_id,device_key,enabled,call_enabled,repeat_minutes,notify_after,updated_at)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
                   device_key=excluded.device_key,enabled=excluded.enabled,
                   call_enabled=excluded.call_enabled,repeat_minutes=excluded.repeat_minutes,
                   notify_after=excluded.notify_after,updated_at=excluded.updated_at""",
                (account_id, stored_key, int(enabled), int(call_enabled), repeat_minutes, notify_after, now),
            )
        return self.get_bark_settings(account_id)

    def bark_notification_for_event(self, account_id: int, event_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT e.id AS server_event_id,e.device_id,e.event_type,e.server_name,e.role_name,
                   e.empty_slots,e.occurred_at,e.acked_at,e.bark_last_sent_at,e.bark_send_count,
                   b.device_key,b.enabled,b.call_enabled,b.repeat_minutes,
                   d.display_name,d.hostname
                   FROM events e JOIN bark_settings b ON b.account_id=e.account_id
                   LEFT JOIN devices d ON d.device_id=e.device_id
                   WHERE e.account_id=? AND e.id=?""",
                (account_id, event_id),
            ).fetchone()
        return dict(row) if row else None

    def due_bark_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.id AS server_event_id,e.account_id,e.device_id,e.event_type,e.server_name,
                   e.role_name,e.empty_slots,e.occurred_at,e.acked_at,e.bark_last_sent_at,e.bark_send_count,
                   b.device_key,b.enabled,b.call_enabled,b.repeat_minutes,
                   d.display_name,d.hostname
                   FROM events e JOIN bark_settings b ON b.account_id=e.account_id
                   LEFT JOIN devices d ON d.device_id=e.device_id
                   WHERE e.acked_at IS NULL AND b.enabled=1 AND b.device_key<>''
                   AND e.received_at>=COALESCE(b.notify_after,e.received_at)
                   AND (e.bark_last_sent_at IS NULL OR
                        (b.repeat_minutes>0 AND
                         (julianday('now')-julianday(e.bark_last_sent_at))*1440>=b.repeat_minutes))
                   ORDER BY e.id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_bark_sent(self, event_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE events SET bark_last_sent_at=?,bark_send_count=COALESCE(bark_send_count,0)+1 WHERE id=?",
                (utc_now(), event_id),
            )

    def list_devices(self, account_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT device_id,display_name,note,hostname,app_version,ip_address,last_seen_at,created_at,disabled
                   FROM devices WHERE account_id=? AND removed_at IS NULL ORDER BY created_at,device_id""",
                (account_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["disabled"] = bool(item["disabled"])
        return result

    def list_device_status(self, account_id: int) -> list[dict[str, Any]]:
        devices = self.list_devices(account_id)
        with self._connect() as connection:
            windows = connection.execute(
                """SELECT s.device_id,s.window_id,s.title,s.server_name,s.role_name,s.character_id,s.status,
                   s.status_text,s.empty_slots,s.occupied_slots,s.confidence,s.occlusion_percent,s.minimized,
                   s.monitoring,s.updated_at,s.received_at,s.cash,s.bank_money,s.money_confirmed_at,s.money_readable FROM device_window_status s
                   JOIN devices d ON d.device_id=s.device_id WHERE d.account_id=? AND d.removed_at IS NULL
                   ORDER BY s.device_id,s.window_id""",
                (account_id,),
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in windows:
            item = dict(row)
            item["minimized"] = bool(item["minimized"])
            item["monitoring"] = bool(item["monitoring"])
            item["money_readable"] = bool(item["money_readable"])
            grouped.setdefault(str(item.pop("device_id")), []).append(item)
        for item in devices:
            item["windows"] = grouped.get(str(item["device_id"]), [])
        return devices

    def purge_acknowledged_events(self, retention_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(timespec="milliseconds")
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM events WHERE acked_at IS NOT NULL AND acked_at<?", (cutoff,))
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

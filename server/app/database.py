"""SQLite persistence for accounts, pairing, devices, status and alarms."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import cash_ledger
from .panel_log_merge import BOUNDARY_TYPES, POSITION, merge_confirmation, remember_confirmation, target_message


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
                CREATE TABLE IF NOT EXISTS monitor_target_receipts (
                    device_id TEXT NOT NULL, client_event_id TEXT NOT NULL, account_id INTEGER NOT NULL,
                    log_id INTEGER, occurred_at TEXT NOT NULL, received_at TEXT NOT NULL,
                    PRIMARY KEY(device_id,client_event_id),
                    FOREIGN KEY(log_id) REFERENCES monitor_logs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS monitor_log_settings (
                    account_id INTEGER PRIMARY KEY, occlusion_enabled INTEGER NOT NULL,
                    occlusion_percent REAL NOT NULL, occlusion_seconds INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_screenshots (
                    log_id INTEGER PRIMARY KEY REFERENCES monitor_logs(id) ON DELETE CASCADE,
                    object_key TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    ready INTEGER NOT NULL DEFAULT 0, upload_token TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_snapshot_settings (
                    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    interval_minutes INTEGER NOT NULL DEFAULT 5
                );
                CREATE TABLE IF NOT EXISTS live_snapshots (
                    device_id TEXT NOT NULL, window_id TEXT NOT NULL,
                    object_key TEXT NOT NULL, captured_at TEXT NOT NULL,
                    received_at TEXT NOT NULL, request_id TEXT,
                    PRIMARY KEY(device_id,window_id),
                    FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS live_snapshot_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                    window_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    server_name TEXT,
                    role_name TEXT,
                    object_key TEXT NOT NULL UNIQUE,
                    captured_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    event_type TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_history_character
                    ON live_snapshot_history(account_id,character_id,id DESC);
                CREATE INDEX IF NOT EXISTS idx_live_history_captured
                    ON live_snapshot_history(captured_at);
                CREATE TABLE IF NOT EXISTS live_snapshot_requests (
                    request_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
                    device_id TEXT NOT NULL, window_id TEXT NOT NULL,
                    requested_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    completed_at TEXT, error TEXT,
                    FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS monitor_occlusion_state (
                    account_id INTEGER NOT NULL, device_id TEXT NOT NULL, window_id TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_sample REAL NOT NULL,
                    PRIMARY KEY(account_id,device_id,window_id)
                );
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
                CREATE TABLE IF NOT EXISTS game_roles (
                    character_id TEXT PRIMARY KEY,
                    server_name TEXT, role_name TEXT,
                    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_role_accounts (
                    character_id TEXT NOT NULL REFERENCES game_roles(character_id),
                    account_id INTEGER NOT NULL REFERENCES accounts(id),
                    last_device_id TEXT, last_window_id TEXT, last_seen_at TEXT,
                    PRIMARY KEY(character_id,account_id)
                );
                CREATE TABLE IF NOT EXISTS game_role_vitals (
                    character_id TEXT NOT NULL REFERENCES game_roles(character_id),
                    account_id INTEGER NOT NULL REFERENCES accounts(id),
                    device_id TEXT NOT NULL,
                    character_level INTEGER NOT NULL,
                    stamina_current INTEGER NOT NULL,
                    stamina_max INTEGER NOT NULL,
                    recovery_per_5m INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    full_at TEXT NOT NULL,
                    PRIMARY KEY(character_id,account_id)
                );
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
            self._ensure_column(connection, "devices", "machine_id", "TEXT")
            self._ensure_column(connection, "devices", "created_at", "TEXT")
            self._ensure_column(connection, "devices", "disabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "devices", "removed_at", "TEXT")
            self._ensure_column(connection, "devices", "protocol_version", "INTEGER")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_machine
                ON devices(account_id,machine_id) WHERE machine_id IS NOT NULL AND removed_at IS NULL""")
            self._ensure_column(connection, "events", "account_id", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "events", "bark_last_sent_at", "TEXT")
            self._ensure_column(connection, "events", "bark_send_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "bark_settings", "notify_after", "TEXT")
            self._ensure_column(connection, "monitor_logs", "panel_type", "TEXT")
            self._ensure_column(connection, "monitor_logs", "confidence", "REAL")
            self._ensure_column(connection, "monitor_logs", "message", "TEXT")
            for name, kind in (("target_name", "TEXT"), ("target_level", "INTEGER"),
                               ("target_confidence", "REAL"), ("target_status", "TEXT"), ("target_confirmed_at", "TEXT")):
                self._ensure_column(connection, "monitor_logs", name, kind)
            connection.execute(f"""CREATE INDEX IF NOT EXISTS idx_logs_target_session
                ON monitor_logs(account_id,device_id,window_id,{POSITION} DESC,id DESC)
                WHERE event_type IN ({BOUNDARY_TYPES})""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_logs_event ON monitor_logs(account_id,event_type,id DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_logs_panel ON monitor_logs(account_id,panel_type,id DESC)")
            for name, kind in (("cash", "INTEGER"), ("bank_money", "INTEGER"), ("money_confirmed_at", "TEXT"), ("money_readable", "INTEGER NOT NULL DEFAULT 0")):
                self._ensure_column(connection, "device_window_status", name, kind)
            self._ensure_column(connection, "live_snapshot_history", "event_type", "TEXT")
            for name in ("last_device_id", "last_window_id", "last_seen_at"):
                self._ensure_column(connection, "game_role_accounts", name, "TEXT")
            now = utc_now()
            cash_ledger.initialize(connection)
            connection.execute(
                "INSERT OR IGNORE INTO accounts(id, username, display_name, password_hash, created_at, disabled) VALUES (1, NULL, '原有主机', NULL, ?, 0)",
                (now,),
            )
            connection.execute("UPDATE devices SET created_at=COALESCE(created_at,last_seen_at,?)", (now,))
            connection.execute("""INSERT OR IGNORE INTO game_roles(character_id,server_name,role_name,first_seen_at,last_seen_at)
                SELECT s.character_id,s.server_name,s.role_name,MIN(s.received_at),MAX(s.received_at)
                FROM device_window_status s WHERE s.character_id IS NOT NULL AND s.character_id<>''
                GROUP BY s.character_id""")
            connection.execute("""INSERT OR IGNORE INTO game_role_accounts(character_id,account_id)
                SELECT DISTINCT s.character_id,d.account_id FROM device_window_status s
                JOIN devices d ON d.device_id=s.device_id JOIN game_roles r ON r.character_id=s.character_id
                WHERE s.character_id IS NOT NULL AND s.character_id<>''""")
            connection.execute("""INSERT OR IGNORE INTO game_roles(character_id,server_name,role_name,first_seen_at,last_seen_at)
                SELECT h.character_id,h.server_name,h.role_name,MIN(h.received_at),MAX(h.received_at)
                FROM live_snapshot_history h WHERE h.character_id<>'' GROUP BY h.character_id""")
            connection.execute("""INSERT OR IGNORE INTO game_role_accounts(character_id,account_id)
                SELECT DISTINCT h.character_id,h.account_id FROM live_snapshot_history h
                JOIN game_roles r ON r.character_id=h.character_id WHERE h.character_id<>''""")
            connection.execute("""UPDATE game_role_accounts SET
                last_device_id=(SELECT s.device_id FROM device_window_status s JOIN devices d ON d.device_id=s.device_id
                    WHERE s.character_id=game_role_accounts.character_id AND d.account_id=game_role_accounts.account_id
                    ORDER BY s.received_at DESC LIMIT 1),
                last_window_id=(SELECT s.window_id FROM device_window_status s JOIN devices d ON d.device_id=s.device_id
                    WHERE s.character_id=game_role_accounts.character_id AND d.account_id=game_role_accounts.account_id
                    ORDER BY s.received_at DESC LIMIT 1),
                last_seen_at=(SELECT MAX(s.received_at) FROM device_window_status s JOIN devices d ON d.device_id=s.device_id
                    WHERE s.character_id=game_role_accounts.character_id AND d.account_id=game_role_accounts.account_id)
                WHERE last_seen_at IS NULL""")
            self._backfill_event_snapshots(connection)

    @classmethod
    def _backfill_event_snapshots(cls, connection: sqlite3.Connection) -> None:
        """Make existing unexpired event evidence visible in role screenshot history."""
        now = datetime.now(UTC)
        rows = connection.execute("""SELECT s.object_key,s.expires_at,l.id,l.account_id,l.device_id,
            l.window_id,l.character_id,l.server_name,l.role_name,l.event_type,l.panel_type,l.occurred_at,l.received_at
            FROM monitor_screenshots s JOIN monitor_logs l ON l.id=s.log_id
            WHERE s.ready=1 AND l.character_id IS NOT NULL AND l.character_id<>''
            AND NOT EXISTS (SELECT 1 FROM live_snapshot_history h WHERE h.object_key=s.object_key)""").fetchall()
        for row in rows:
            try:
                captured = datetime.fromisoformat(row["occurred_at"])
                captured = captured.replace(tzinfo=UTC) if captured.tzinfo is None else captured.astimezone(UTC)
                if captured < now - timedelta(days=30) or datetime.fromisoformat(row["expires_at"]) <= now:
                    continue
            except (TypeError, ValueError):
                continue
            stamp = captured.isoformat(timespec="milliseconds")
            window_id = row["window_id"] or f"event:{row['id']}"
            cls._remember_game_role(connection, row["account_id"], row["device_id"], dict(row), row["received_at"])
            connection.execute("""INSERT OR IGNORE INTO live_snapshot_history
                (account_id,device_id,window_id,character_id,server_name,role_name,
                 object_key,captured_at,received_at,event_type) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (row["account_id"], row["device_id"], window_id, row["character_id"],
                 row["server_name"], row["role_name"], row["object_key"], stamp,
                 row["received_at"], f"{row['event_type']}:{row['panel_type'] or ''}"))
            cls._upsert_latest_snapshot(connection, row["device_id"], window_id,
                                        row["object_key"], stamp, None, row["received_at"])

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def record_cash(self, account_id: int, device_id: str, payload: dict[str, Any]) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return cash_ledger.record(connection, account_id, device_id, payload)

    def cash_statistics(self, account_id: int, day: str) -> dict[str, Any]:
        with self._connect() as connection:
            return cash_ledger.statistics(connection, account_id, day)

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
                """SELECT d.account_id FROM devices d JOIN accounts a ON a.id=d.account_id
                   WHERE d.device_id=? AND d.credential_hash=? AND d.disabled=0
                   AND d.removed_at IS NULL AND a.disabled=0""",
                (device_id, credential_hash),
            ).fetchone()
        return int(row["account_id"]) if row else None

    def login_device(
        self, account_id: int, installation_id: str, credential_hash: str,
        hostname: str | None, app_version: str | None, protocol_version: int | None,
        machine_id: str | None = None,
    ) -> str:
        """Reuse the same physical machine across reinstallations."""
        now = utc_now()
        with self._connect() as connection:
            existing = None
            if machine_id:
                existing = connection.execute(
                    """SELECT device_id,disabled,removed_at FROM devices
                       WHERE account_id=? AND machine_id=? AND removed_at IS NULL
                       ORDER BY created_at LIMIT 1""", (account_id, machine_id),
                ).fetchone()
            if not existing:
                existing = connection.execute(
                    """SELECT device_id,disabled,removed_at FROM devices
                       WHERE account_id=? AND installation_id=? ORDER BY created_at DESC LIMIT 1""",
                    (account_id, installation_id),
                ).fetchone()
            if not existing and machine_id and hostname:
                existing = self._named_legacy_machine(connection, account_id, hostname, now)
            if existing and not existing["removed_at"]:
                if existing["disabled"]:
                    raise PermissionError("这台机器已在后台禁用，请联系账号管理员")
                device_id = str(existing["device_id"])
                connection.execute(
                    """UPDATE devices SET credential_hash=?,hostname=COALESCE(?,hostname),
                       app_version=COALESCE(?,app_version),protocol_version=?,last_seen_at=?,
                       installation_id=?
                       WHERE device_id=?""",
                    (credential_hash, hostname, app_version, protocol_version, now, installation_id, device_id),
                )
            else:
                device_id = self._new_device_id(connection, account_id)
                default_name = (hostname or "").strip()[:64] or f"新从机 {device_id}"
                connection.execute(
                    """INSERT INTO devices(device_id,account_id,display_name,hostname,app_version,
                       protocol_version,last_seen_at,credential_hash,installation_id,machine_id,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (device_id, account_id, default_name, hostname, app_version,
                     protocol_version, now, credential_hash, installation_id, machine_id, now),
                )
        return device_id

    @staticmethod
    def _named_legacy_machine(connection: sqlite3.Connection, account_id: int, hostname: str,
                              now: str, exclude: str = "") -> sqlite3.Row | None:
        cutoff = (datetime.fromisoformat(now) - timedelta(seconds=60)).isoformat()
        rows = connection.execute(
            """SELECT device_id,disabled,removed_at FROM devices
               WHERE account_id=? AND hostname=? AND device_id<>? AND removed_at IS NULL
               AND disabled=0 AND display_name<>hostname AND last_seen_at<?
               AND machine_id IS NULL""",
            (account_id, hostname, exclude, cutoff),
        ).fetchall()
        return rows[0] if len(rows) == 1 else None

    def bind_machine(self, account_id: int, device_id: str, machine_id: str, hostname: str | None) -> str:
        """Attach a stable machine identity and recover one unambiguous renamed legacy device."""
        now = utc_now()
        with self._connect() as connection:
            current = connection.execute(
                """SELECT * FROM devices WHERE account_id=? AND device_id=? AND removed_at IS NULL AND disabled=0""",
                (account_id, device_id),
            ).fetchone()
            if current is None:
                raise PermissionError("从机凭据已失效")
            target = connection.execute(
                """SELECT device_id,disabled FROM devices WHERE account_id=? AND machine_id=?
                   AND removed_at IS NULL ORDER BY created_at LIMIT 1""",
                (account_id, machine_id),
            ).fetchone()
            if target is None and hostname and current["display_name"] == current["hostname"]:
                target = self._named_legacy_machine(connection, account_id, hostname, now, device_id)
            if target is not None and target["disabled"]:
                raise PermissionError("这台机器已在后台禁用，请联系账号管理员")
            canonical = str(target["device_id"]) if target is not None else device_id
            if canonical != device_id:
                connection.execute(
                    """UPDATE devices SET credential_hash=?,installation_id=?,machine_id=?,
                       hostname=COALESCE(?,hostname),app_version=?,protocol_version=?,last_seen_at=?
                       WHERE device_id=?""",
                    (current["credential_hash"], current["installation_id"], machine_id,
                     hostname, current["app_version"], current["protocol_version"], now, canonical),
                )
                connection.execute(
                    "UPDATE devices SET removed_at=?,disabled=1,credential_hash=NULL WHERE device_id=?",
                    (now, device_id),
                )
                connection.execute("DELETE FROM device_window_status WHERE device_id=?", (device_id,))
            else:
                connection.execute(
                    "UPDATE devices SET machine_id=?,hostname=COALESCE(?,hostname) WHERE device_id=?",
                    (machine_id, hostname, device_id),
                )
        return canonical

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

    def set_device_protocol(self, device_id: str, protocol_version: int | None) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE devices SET protocol_version=? WHERE device_id=?",
                               (protocol_version, device_id))

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
            active_ids = {str(window.get("window_id", "")) for window in windows}
            for row in connection.execute("SELECT window_id FROM monitor_occlusion_state WHERE device_id=?", (device_id,)).fetchall():
                if row["window_id"] not in active_ids:
                    connection.execute("DELETE FROM monitor_occlusion_state WHERE device_id=? AND window_id=?", (device_id, row["window_id"]))
            for window in windows:
                for kind, active in (
                    ("window_occluded", bool(device) and self._qualifies_occlusion(
                        connection, int(device["account_id"]), device_id, window, received_at)),
                    ("window_minimized", bool(window.get("minimized"))),
                ):
                    if active and device:
                        occurred = str(window.get("updated_at") or reported_at or received_at)
                        self._insert_monitor_log(connection, int(device["account_id"]), device_id, {
                            **window, "event_type": kind, "occurred_at": occurred,
                            "event_id": f"status:{reported_at or occurred}:{window['window_id']}:{kind}",
                        }, received_at)
            for previous in connection.execute(
                "SELECT window_id FROM device_window_status WHERE device_id=? AND status<>'window_closed'",
                (device_id,),
            ).fetchall():
                if previous["window_id"] not in active_ids:
                    connection.execute(
                        """UPDATE device_window_status SET status='window_closed',status_text='窗口已关闭',
                           empty_slots=NULL,occupied_slots=NULL,confidence=NULL,occlusion_percent=NULL,
                           minimized=0,monitoring=0,money_readable=0 WHERE device_id=? AND window_id=?""",
                        (device_id, previous["window_id"]),
                    )
            for window in windows:
                character_id = str(window.get("character_id") or "")
                if character_id:
                    self._remember_game_role(connection, int(device["account_id"]), device_id, window, received_at)
                    self._upsert_role_vitals(connection, int(device["account_id"]), device_id, window)
                    connection.execute(
                        """DELETE FROM device_window_status WHERE device_id=? AND character_id=?
                           AND window_id<>? AND status='window_closed'""",
                        (device_id, character_id, window["window_id"]),
                    )
            connection.executemany(
                """INSERT INTO device_window_status(device_id,window_id,title,server_name,role_name,character_id,
                   status,status_text,empty_slots,occupied_slots,confidence,occlusion_percent,minimized,monitoring,
                   updated_at,received_at,cash,bank_money,money_confirmed_at,money_readable)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(device_id,window_id) DO UPDATE SET
                   title=excluded.title,server_name=excluded.server_name,role_name=excluded.role_name,
                   character_id=excluded.character_id,status=excluded.status,status_text=excluded.status_text,
                   empty_slots=excluded.empty_slots,occupied_slots=excluded.occupied_slots,
                   confidence=excluded.confidence,occlusion_percent=excluded.occlusion_percent,
                   minimized=excluded.minimized,monitoring=excluded.monitoring,
                   updated_at=excluded.updated_at,received_at=excluded.received_at,
                   cash=excluded.cash,bank_money=excluded.bank_money,
                   money_confirmed_at=excluded.money_confirmed_at,money_readable=excluded.money_readable""",
                [(
                    device_id, str(w.get("window_id", "")), w.get("title"), w.get("server_name"),
                    w.get("role_name"), w.get("character_id"), str(w.get("status") or "unknown"),
                    w.get("status_text"), w.get("empty_slots"), w.get("occupied_slots"), w.get("confidence"),
                    w.get("occlusion_percent"), int(bool(w.get("minimized"))), int(bool(w.get("monitoring", True))),
                    str(w.get("updated_at") or received_at), received_at,
                    w.get("cash"), None, w.get("money_confirmed_at"), int(bool(w.get("money_readable"))),
                ) for w in windows if str(w.get("window_id", ""))],
            )
        return received_at

    @staticmethod
    def _remember_game_role(connection: sqlite3.Connection, account_id: int, device_id: str,
                            payload: dict[str, Any], seen_at: str) -> None:
        character_id = str(payload.get("character_id") or "").strip()
        if not character_id:
            return
        connection.execute("""INSERT INTO game_roles
            (character_id,server_name,role_name,first_seen_at,last_seen_at) VALUES (?,?,?,?,?)
            ON CONFLICT(character_id) DO UPDATE SET
            server_name=COALESCE(NULLIF(excluded.server_name,''),game_roles.server_name),
            role_name=COALESCE(NULLIF(excluded.role_name,''),game_roles.role_name),
            last_seen_at=MAX(game_roles.last_seen_at,excluded.last_seen_at)""",
            (character_id, payload.get("server_name"), payload.get("role_name"), seen_at, seen_at))
        connection.execute("""INSERT INTO game_role_accounts
            (character_id,account_id,last_device_id,last_window_id,last_seen_at) VALUES (?,?,?,?,?)
            ON CONFLICT(character_id,account_id) DO UPDATE SET
            last_device_id=CASE WHEN excluded.last_seen_at>=COALESCE(game_role_accounts.last_seen_at,'') THEN excluded.last_device_id ELSE game_role_accounts.last_device_id END,
            last_window_id=CASE WHEN excluded.last_seen_at>=COALESCE(game_role_accounts.last_seen_at,'') THEN excluded.last_window_id ELSE game_role_accounts.last_window_id END,
            last_seen_at=MAX(COALESCE(game_role_accounts.last_seen_at,''),excluded.last_seen_at)""",
            (character_id, account_id, device_id, payload.get("window_id"), seen_at))

    @staticmethod
    def _upsert_role_vitals(connection: sqlite3.Connection, account_id: int, device_id: str,
                            payload: dict[str, Any]) -> None:
        character_id = str(payload.get("character_id") or "").strip()
        try:
            level = int(payload["character_level"])
            current = int(payload["stamina_current"])
            maximum = int(payload["stamina_max"])
            observed = datetime.fromisoformat(str(payload["stamina_observed_at"]))
        except (KeyError, ValueError, TypeError):
            return
        if (not character_id or observed.tzinfo is None or not 1 <= level <= 175
                or not 1 <= maximum <= 99999 or not 0 <= current <= maximum):
            return
        observed = observed.astimezone(UTC)
        if observed > datetime.now(UTC) + timedelta(minutes=2):
            return
        recovery = maximum // 100 + level // 50 + 2
        ticks = (maximum - current + recovery - 1) // recovery
        connection.execute("""INSERT INTO game_role_vitals
            (character_id,account_id,device_id,character_level,stamina_current,stamina_max,
             recovery_per_5m,observed_at,full_at) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(character_id,account_id) DO UPDATE SET
            device_id=excluded.device_id,character_level=excluded.character_level,
            stamina_current=excluded.stamina_current,stamina_max=excluded.stamina_max,
            recovery_per_5m=excluded.recovery_per_5m,observed_at=excluded.observed_at,
            full_at=excluded.full_at
            WHERE julianday(excluded.observed_at)>julianday(game_role_vitals.observed_at)""",
            (character_id, account_id, device_id, level, current, maximum, recovery,
             observed.isoformat(timespec="milliseconds"),
             (observed + timedelta(minutes=ticks * 5)).isoformat(timespec="milliseconds")))

    @staticmethod
    def _log_settings(connection: sqlite3.Connection, account_id: int) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM monitor_log_settings WHERE account_id=?", (account_id,)).fetchone()
        return {"occlusion_enabled": bool(row["occlusion_enabled"]) if row else True,
                "occlusion_percent": float(row["occlusion_percent"]) if row else 60.0,
                "occlusion_seconds": int(row["occlusion_seconds"]) if row else 5}

    def get_monitor_log_settings(self, account_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            return self._log_settings(connection, account_id)

    def update_monitor_log_settings(self, account_id: int, values: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            changed = self._log_settings(connection, account_id) != values
            connection.execute("""INSERT INTO monitor_log_settings VALUES (?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET occlusion_enabled=excluded.occlusion_enabled,
                occlusion_percent=excluded.occlusion_percent,occlusion_seconds=excluded.occlusion_seconds""",
                (account_id, int(values["occlusion_enabled"]), values["occlusion_percent"], values["occlusion_seconds"]))
            if changed:
                connection.execute("DELETE FROM monitor_occlusion_state WHERE account_id=?", (account_id,))
            return self._log_settings(connection, account_id)

    def _qualifies_occlusion(self, connection: sqlite3.Connection, account_id: int, device_id: str,
                            window: dict[str, Any], received_at: str) -> bool:
        policy = self._log_settings(connection, account_id)
        key = (account_id, device_id, str(window.get("window_id") or ""))
        percentage = float(window.get("occlusion_percent") or 0)
        eligible = (policy["occlusion_enabled"] and percentage > 0 and percentage >= policy["occlusion_percent"]
                    and window.get("monitoring", True) and not window.get("minimized") and bool(key[2]))
        if not eligible:
            connection.execute("DELETE FROM monitor_occlusion_state WHERE account_id=? AND device_id=? AND window_id=?", key)
            return False
        try:
            now = datetime.fromisoformat(received_at).timestamp()
            sample = datetime.fromisoformat(str(window.get("updated_at") or window.get("occurred_at"))).timestamp()
        except (ValueError, TypeError):
            connection.execute("DELETE FROM monitor_occlusion_state WHERE account_id=? AND device_id=? AND window_id=?", key)
            return False
        previous = connection.execute("SELECT * FROM monitor_occlusion_state WHERE account_id=? AND device_id=? AND window_id=?", key).fetchone()
        if previous and sample <= previous["last_sample"]:
            return False
        first = previous["first_seen"] if previous and 0 <= now - previous["last_seen"] <= 15 else now
        connection.execute("INSERT OR REPLACE INTO monitor_occlusion_state VALUES (?,?,?,?,?,?)", (*key, first, now, sample))
        return now - first >= policy["occlusion_seconds"]

    @staticmethod
    def _insert_monitor_log(
        connection: sqlite3.Connection, account_id: int, device_id: str,
        payload: dict[str, Any], received_at: str,
    ) -> None:
        if merge_confirmation(connection, account_id, device_id, payload, received_at):
            return
        payload = dict(payload)
        if payload.get("panel_type") in {"give", "trade"} and payload.get("target_status") is not None:
            payload["message"] = target_message(payload, opened=payload["event_type"] == "panel_opened")
        cursor = connection.execute(
            """INSERT OR IGNORE INTO monitor_logs(account_id,device_id,client_event_id,event_type,
               window_id,server_name,role_name,character_id,occlusion_percent,minimized,
               occurred_at,received_at,reason,panel_type,confidence,message,target_name,target_level,target_confidence,target_status,target_confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, device_id, payload["event_id"], payload["event_type"], payload.get("window_id"),
             payload.get("server_name"), payload.get("role_name"), payload.get("character_id"),
             payload.get("occlusion_percent"),
             None if payload.get("minimized") is None else int(payload["minimized"]),
             str(payload["occurred_at"]), received_at, payload.get("reason"), payload.get("panel_type"),
             payload.get("confidence"), payload.get("message"), payload.get("target_name"), payload.get("target_level"),
             payload.get("target_confidence"), payload.get("target_status"),
             str(payload["occurred_at"]) if payload.get("target_status") == "recognized" else None),
        )

        if cursor.rowcount and payload.get("character_id"):
            RelayDatabase._remember_game_role(connection, account_id, device_id, payload, received_at)
            RelayDatabase._upsert_role_vitals(connection, account_id, device_id, payload)

        if payload["event_type"] == "panel_target_changed" and cursor.rowcount:
            remember_confirmation(connection, account_id, device_id, payload, received_at, int(cursor.lastrowid))

    def insert_monitor_log(self, account_id: int, device_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            received = utc_now()
            if payload["event_type"] == "window_occluded" and not self._qualifies_occlusion(connection, account_id, device_id, payload, received):
                return
            self._insert_monitor_log(connection, account_id, device_id, payload, received)

    def list_monitor_logs(
        self, account_id: int, *, before_id: int = 0, limit: int = 100, event_type: str = "", panel_type: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["l.account_id=?"]
        parameters: list[Any] = [account_id]
        if before_id:
            clauses.append("l.id<?")
            parameters.append(before_id)
        if event_type:
            clauses.append("l.event_type=?")
            parameters.append(event_type)
        if panel_type:
            clauses.append("l.panel_type=?")
            parameters.append(panel_type)
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT l.*,d.display_name,d.hostname,
                   s.ready AS screenshot_ready,s.expires_at AS screenshot_expires_at FROM monitor_logs l
                   LEFT JOIN devices d ON d.device_id=l.device_id
                   LEFT JOIN monitor_screenshots s ON s.log_id=l.id
                   WHERE {' AND '.join(clauses)} ORDER BY l.id DESC LIMIT ?""",
                parameters,
            ).fetchall()
        return [{**dict(row), "minimized": None if row["minimized"] is None else bool(row["minimized"])}
                for row in rows]

    def screenshot_log(self, account_id: int, device_id: str, event_id: str):
        with self._connect() as connection:
            row = connection.execute("""SELECT l.* FROM monitor_logs l WHERE l.account_id=? AND l.device_id=?
                AND (l.client_event_id=? OR l.id IN (SELECT log_id FROM monitor_target_receipts
                     WHERE account_id=? AND device_id=? AND client_event_id=?))""",
                (account_id, device_id, event_id, account_id, device_id, event_id)).fetchone()
        return dict(row) if row else None

    def claim_screenshot(self, account_id: int, log_id: int, token: str):
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM monitor_screenshots WHERE log_id=?", (log_id,)).fetchone()
            if row and row["ready"]:
                return "ready", row["object_key"]
            if row and datetime.fromisoformat(row["created_at"]) > now - timedelta(minutes=5):
                return "busy", row["object_key"]
            key = f"screenshots/{account_id}/{log_id}/{token}.jpg"
            connection.execute("""INSERT OR REPLACE INTO monitor_screenshots
                (log_id,object_key,created_at,expires_at,ready,upload_token) VALUES (?,?,?,?,0,?)""",
                (log_id, key, now.isoformat(), (now + timedelta(days=30)).isoformat(), token))
            return "claimed", key

    def finish_screenshot(self, log_id: int, token: str, *, success: bool):
        with self._connect() as connection:
            if success:
                screenshot = connection.execute("""SELECT s.object_key,l.account_id,l.device_id,l.window_id,
                    l.character_id,l.server_name,l.role_name,l.event_type,l.panel_type,l.occurred_at
                    FROM monitor_screenshots s JOIN monitor_logs l ON l.id=s.log_id
                    WHERE s.log_id=? AND s.upload_token=?""", (log_id, token)).fetchone()
                if screenshot is None:
                    raise LookupError("截图上传记录不存在")
                connection.execute("UPDATE monitor_screenshots SET ready=1 WHERE log_id=? AND upload_token=?",
                                   (log_id, token))
                if screenshot["character_id"]:
                    # Event evidence joins the same chronological role history as scheduled captures.
                    captured_at = datetime.fromisoformat(screenshot["occurred_at"])
                    if captured_at.tzinfo is None:
                        captured_at = captured_at.replace(tzinfo=UTC)
                    stamp = captured_at.astimezone(UTC).isoformat(timespec="milliseconds")
                    window_id = screenshot["window_id"] or f"event:{log_id}"
                    connection.execute("""INSERT OR IGNORE INTO live_snapshot_history
                        (account_id,device_id,window_id,character_id,server_name,role_name,
                         object_key,captured_at,received_at,event_type) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (screenshot["account_id"], screenshot["device_id"], window_id,
                         screenshot["character_id"], screenshot["server_name"], screenshot["role_name"],
                         screenshot["object_key"], stamp, utc_now(),
                         f"{screenshot['event_type']}:{screenshot['panel_type'] or ''}"))
                    self._upsert_latest_snapshot(connection, screenshot["device_id"], window_id,
                                                 screenshot["object_key"], stamp, None)
            else:
                connection.execute("DELETE FROM monitor_screenshots WHERE log_id=? AND upload_token=? AND ready=0",
                                   (log_id, token))

    def get_screenshot(self, account_id: int, log_id: int):
        with self._connect() as connection:
            row = connection.execute("""SELECT s.* FROM monitor_screenshots s JOIN monitor_logs l ON l.id=s.log_id
                WHERE l.account_id=? AND l.id=? AND s.ready=1""", (account_id, log_id)).fetchone()
        return dict(row) if row else None

    def list_events(self, account_id: int, *, pending_only: bool, after_id: int, limit: int,
                    latest: bool = False) -> list[dict[str, Any]]:
        predicates = ["account_id=?", "id>?"]
        parameters: list[Any] = [account_id, after_id]
        if pending_only:
            predicates.append("acked_at IS NULL")
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT id,device_id,client_event_id,event_type,server_name,role_name,character_id,
                   empty_slots,confidence,occlusion_percent,minimized,occurred_at,received_at,payload_json,acked_at
                   FROM events WHERE {' AND '.join(predicates)} ORDER BY id {'DESC' if latest else 'ASC'} LIMIT ?""",
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

    def device_profile(self, account_id: int, device_id: str) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT display_name,hostname FROM devices WHERE account_id=? AND device_id=? AND removed_at IS NULL",
                (account_id, device_id),
            ).fetchone()
        return {"display_name": str(row["display_name"] or row["hostname"] or device_id)} if row else {}

    def list_devices(self, account_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT device_id,display_name,note,hostname,app_version,protocol_version,ip_address,last_seen_at,created_at,disabled
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

    def list_game_roles(self, account_id: int, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One account-visible card per globally unique game ID, across all devices."""
        with self._connect() as connection:
            registered = connection.execute("""SELECT r.character_id,r.server_name,r.role_name,r.first_seen_at,
                a.last_device_id,a.last_window_id,a.last_seen_at
                FROM game_roles r JOIN game_role_accounts a ON a.character_id=r.character_id
                WHERE a.account_id=?""", (account_id,)).fetchall()
            shots = connection.execute("""SELECT id,character_id,device_id,window_id,captured_at,event_type FROM (
                SELECT id,character_id,device_id,window_id,captured_at,event_type,
                    ROW_NUMBER() OVER (PARTITION BY character_id ORDER BY captured_at DESC,id DESC) AS rank
                FROM live_snapshot_history WHERE account_id=?
            ) WHERE rank=1""", (account_id,)).fetchall()
            vitals = connection.execute("""SELECT character_id,character_level,stamina_current,
                stamina_max,recovery_per_5m,observed_at,full_at FROM game_role_vitals
                WHERE account_id=?""", (account_id,)).fetchall()
        latest_shots = {str(row["character_id"]): dict(row) for row in shots}
        latest_vitals = {str(row["character_id"]): dict(row) for row in vitals}
        candidates: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        now = datetime.now(UTC)
        for device in devices:
            try:
                device_online = not device["disabled"] and (now - datetime.fromisoformat(device["last_seen_at"])).total_seconds() <= 20
            except (KeyError, TypeError, ValueError):
                device_online = False
            for window in device["windows"]:
                character_id = str(window.get("character_id") or "")
                if character_id:
                    candidates.setdefault(character_id, []).append((device, {**window,
                        "role_online": device_online and window["status"] != "window_closed"}))
        result = []
        for role in registered:
            character_id = str(role["character_id"])
            options = candidates.get(character_id, [])
            # Prefer a live reporting window. Within the same state, the latest report owns the card.
            options.sort(key=lambda pair: (pair[1]["role_online"], pair[1].get("received_at") or ""), reverse=True)
            device, window = options[0] if options else (next((d for d in devices if d["device_id"] == role["last_device_id"]), {}), {})
            shot = latest_shots.get(character_id)
            stamina = latest_vitals.get(character_id)
            result.append({**window, "character_id": character_id,
                "server_name": window.get("server_name") or role["server_name"],
                "role_name": window.get("role_name") or role["role_name"],
                "device_id": device.get("device_id") or role["last_device_id"] or (shot or {}).get("device_id"),
                "device_name": device.get("display_name") or device.get("hostname") or "曾监控的从机",
                "role_online": bool(window.get("role_online")),
                "first_seen_at": role["first_seen_at"], "received_at": window.get("received_at") or role["last_seen_at"],
                "screenshot_at": shot["captured_at"] if shot else window.get("screenshot_at"),
                "screenshot_id": shot["id"] if shot else None,
                "screenshot_event_type": shot["event_type"] if shot else None,
                "screenshot_device_id": shot["device_id"] if shot else device.get("device_id"),
                "screenshot_window_id": shot["window_id"] if shot else window.get("window_id"),
                "stamina_current": stamina["stamina_current"] if stamina else None,
                "stamina_max": stamina["stamina_max"] if stamina else None,
                "character_level": stamina["character_level"] if stamina else None,
                "stamina_recovery_per_5m": stamina["recovery_per_5m"] if stamina else None,
                "stamina_observed_at": stamina["observed_at"] if stamina else None,
                "stamina_full_at": stamina["full_at"] if stamina else None})
        result.sort(key=lambda role: (role["role_online"], role.get("received_at") or role["first_seen_at"]), reverse=True)
        return result

    def backfill_closed_windows(self) -> int:
        """Recover previously discarded window cards when a recent screenshot remains."""
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        restored = 0
        with self._connect() as connection:
            snapshots = connection.execute(
                """SELECT l.device_id,l.window_id,l.captured_at FROM live_snapshots l
                   JOIN devices d ON d.device_id=l.device_id
                   LEFT JOIN device_window_status s ON s.device_id=l.device_id AND s.window_id=l.window_id
                   WHERE d.removed_at IS NULL AND s.window_id IS NULL AND l.captured_at>=?
                   ORDER BY l.captured_at DESC""", (cutoff,),
            ).fetchall()
            for shot in snapshots:
                identity = connection.execute(
                    """SELECT server_name,role_name,character_id FROM live_snapshot_history
                       WHERE device_id=? AND window_id=? AND character_id IS NOT NULL
                       ORDER BY id DESC LIMIT 1""", (shot["device_id"], shot["window_id"]),
                ).fetchone()
                if identity is None:
                    identity = connection.execute(
                        """SELECT server_name,role_name,character_id FROM monitor_logs
                           WHERE device_id=? AND window_id=? AND character_id IS NOT NULL
                           ORDER BY id DESC LIMIT 1""", (shot["device_id"], shot["window_id"]),
                    ).fetchone()
                character_id = identity["character_id"] if identity else None
                if character_id and connection.execute(
                    "SELECT 1 FROM device_window_status WHERE device_id=? AND character_id=? LIMIT 1",
                    (shot["device_id"], character_id),
                ).fetchone():
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO device_window_status
                       (device_id,window_id,server_name,role_name,character_id,status,status_text,
                        minimized,monitoring,updated_at,received_at,money_readable)
                       VALUES (?,?,?,?,?,'window_closed','窗口已关闭',0,0,?,?,0)""",
                    (shot["device_id"], shot["window_id"], identity["server_name"] if identity else None,
                     identity["role_name"] if identity else None, character_id, shot["captured_at"], utc_now()),
                )
                restored += 1
        return restored

    def snapshot_interval(self, account_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT interval_minutes FROM live_snapshot_settings WHERE account_id=?", (account_id,)).fetchone()
        return int(row[0]) if row else 5

    def set_snapshot_interval(self, account_id: int, minutes: int) -> int:
        with self._connect() as connection:
            connection.execute("INSERT INTO live_snapshot_settings(account_id,interval_minutes) VALUES (?,?) ON CONFLICT(account_id) DO UPDATE SET interval_minutes=excluded.interval_minutes", (account_id, minutes))
        return minutes

    def snapshot_window(self, account_id: int, device_id: str, window_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""SELECT s.window_id,s.minimized,s.monitoring,d.last_seen_at,d.disabled
                FROM device_window_status s JOIN devices d ON d.device_id=s.device_id
                WHERE d.account_id=? AND d.removed_at IS NULL AND s.device_id=? AND s.window_id=?""",
                (account_id, device_id, window_id)).fetchone()
        return dict(row) if row else None

    def snapshot_requests(self, device_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT request_id,window_id FROM live_snapshot_requests
                WHERE device_id=? AND completed_at IS NULL AND expires_at>? ORDER BY requested_at""",
                (device_id, utc_now())).fetchall()
        return [dict(row) for row in rows]

    def create_snapshot_request(self, account_id: int, device_id: str, window_id: str, request_id: str) -> None:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("""UPDATE live_snapshot_requests SET completed_at=?,error='已被新的请求替代'
                WHERE device_id=? AND window_id=? AND completed_at IS NULL""", (now.isoformat(), device_id, window_id))
            connection.execute("""INSERT INTO live_snapshot_requests
                (request_id,account_id,device_id,window_id,requested_at,expires_at)
                VALUES (?,?,?,?,?,?)""", (request_id, account_id, device_id, window_id,
                now.isoformat(), (now + timedelta(seconds=45)).isoformat()))

    def snapshot_request(self, account_id: int, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM live_snapshot_requests WHERE account_id=? AND request_id=?", (account_id, request_id)).fetchone()
        return dict(row) if row else None

    def save_live_snapshot(self, device_id: str, window_id: str, key: str, captured_at: str,
                           request_id: str | None, *, keep_history: bool = False) -> None:
        now = utc_now()
        with self._connect() as connection:
            if keep_history:
                window = connection.execute("""SELECT d.account_id,s.character_id,s.server_name,s.role_name
                    FROM device_window_status s JOIN devices d ON d.device_id=s.device_id
                    WHERE s.device_id=? AND s.window_id=? AND d.removed_at IS NULL""",
                    (device_id, window_id)).fetchone()
                if window and window["character_id"]:
                    connection.execute("""INSERT OR IGNORE INTO live_snapshot_history
                        (account_id,device_id,window_id,character_id,server_name,role_name,object_key,captured_at,received_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (window["account_id"], device_id, window_id, window["character_id"], window["server_name"],
                         window["role_name"], key, captured_at, now))
            self._upsert_latest_snapshot(connection, device_id, window_id, key, captured_at, request_id, now)
            if request_id:
                connection.execute("""UPDATE live_snapshot_requests SET completed_at=?
                    WHERE request_id=? AND device_id=? AND window_id=? AND completed_at IS NULL""",
                    (now, request_id, device_id, window_id))

    @staticmethod
    def _upsert_latest_snapshot(connection: sqlite3.Connection, device_id: str, window_id: str,
                                key: str, captured_at: str, request_id: str | None,
                                received_at: str | None = None) -> None:
        connection.execute("""INSERT INTO live_snapshots(device_id,window_id,object_key,captured_at,received_at,request_id)
            VALUES (?,?,?,?,?,?) ON CONFLICT(device_id,window_id) DO UPDATE SET
            object_key=excluded.object_key,captured_at=excluded.captured_at,
            received_at=excluded.received_at,request_id=excluded.request_id
            WHERE julianday(excluded.captured_at)>=julianday(live_snapshots.captured_at)""",
            (device_id, window_id, key, captured_at, received_at or utc_now(), request_id))

    def live_snapshot(self, account_id: int, device_id: str, window_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""SELECT l.object_key,l.captured_at,l.received_at,l.request_id
                FROM live_snapshots l JOIN devices d ON d.device_id=l.device_id
                WHERE d.account_id=? AND d.removed_at IS NULL AND l.device_id=? AND l.window_id=?""",
                (account_id, device_id, window_id)).fetchone()
        return dict(row) if row else None

    def live_snapshot_history(self, account_id: int, character_id: str, *, before: int | None,
                              limit: int) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with self._connect() as connection:
            rows = connection.execute("""SELECT id,device_id,window_id,server_name,role_name,captured_at,event_type
                FROM live_snapshot_history WHERE account_id=? AND character_id=? AND captured_at>=?
                AND (? IS NULL OR (captured_at,id)<(SELECT captured_at,id FROM live_snapshot_history WHERE id=? AND account_id=?))
                ORDER BY captured_at DESC,id DESC LIMIT ?""",
                (account_id, character_id, cutoff, before, before, account_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def historical_snapshot(self, account_id: int, snapshot_id: int) -> dict[str, Any] | None:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with self._connect() as connection:
            row = connection.execute("""SELECT object_key,captured_at FROM live_snapshot_history
                WHERE account_id=? AND id=? AND captured_at>=?""", (account_id, snapshot_id, cutoff)).fetchone()
        return dict(row) if row else None

    def purge_live_snapshot_history(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with self._connect() as connection:
            result = connection.execute("DELETE FROM live_snapshot_history WHERE captured_at<?", (cutoff,))
        return max(0, result.rowcount)

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

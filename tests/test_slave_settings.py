import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.settings_dialog import SlaveSettingsDialog
from hoyo_analyzer import slave_settings as settings_module
from hoyo_analyzer import machine_identity
from hoyo_analyzer.slave_settings import (
    CAPACITY_ALERT_HOST,
    CAPACITY_ALERT_LOCAL,
    CAPACITY_ALERT_NONE,
    SlaveSettings,
    load_slave_settings,
    matches_monitored_window,
    plan_window_restore,
    save_slave_settings,
)


def test_slave_settings_are_saved_and_loaded(tmp_path) -> None:
    path = tmp_path / "runtime" / "slave_settings.json"
    expected = SlaveSettings(
        inventory_capacity_alert_level=CAPACITY_ALERT_LOCAL,
        inventory_capacity_threshold=3,
        relay_url="https://relay.example.com",
        device_id="slave-01",
        display_name="收购电脑01",
        device_token="device-secret-token",
        installation_id="installation-1234567890",
    )

    save_slave_settings(expected, path)

    assert load_slave_settings(path) == expected
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "inventory_capacity_alert_level",
        "inventory_capacity_threshold",
        "relay_url",
        "device_id",
        "display_name",
        "account_username",
        "device_token",
        "installation_id",
        "pairing_code",
            "monitored_windows",
    }


def test_install_directory_settings_migrate_to_local_app_data(tmp_path, monkeypatch) -> None:
    legacy_root = tmp_path / "old-install"
    legacy_file = legacy_root / "runtime" / "slave_settings.json"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(json.dumps({"device_id": "slave-001-005", "device_token": "secret",
                                       "display_name": "宝石商人电脑", "installation_id": "old-install-id"}),
                           encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-data"))
    monkeypatch.setattr(settings_module, "application_root", lambda: legacy_root)
    loaded = load_slave_settings()
    assert loaded.device_id == "slave-001-005"
    assert settings_module.settings_path().exists()
    legacy_file.unlink()
    assert load_slave_settings().display_name == "宝石商人电脑"


def test_machine_identity_survives_install_directory_removal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-data"))
    monkeypatch.setattr(machine_identity, "_windows_machine_guid", lambda: "")
    first = machine_identity.machine_id()
    assert len(first) == 64
    assert machine_identity.machine_id() == first


def test_legacy_alert_modes_are_migrated_safely(tmp_path) -> None:
    path = tmp_path / "slave_settings.json"
    path.write_text('{"inventory_alert":"host"}', encoding="utf-8")
    assert load_slave_settings(path).inventory_capacity_alert_level == CAPACITY_ALERT_HOST

    path.write_text('{"inventory_alert":"local_host"}', encoding="utf-8")
    assert load_slave_settings(path).inventory_capacity_alert_level == CAPACITY_ALERT_LOCAL

    path.write_text('{"inventory_alert":"local"}', encoding="utf-8")
    assert load_slave_settings(path).inventory_capacity_alert_level == CAPACITY_ALERT_LOCAL

    path.write_text('{"inventory_capacity_alert_enabled":true}', encoding="utf-8")
    assert load_slave_settings(path).inventory_capacity_alert_level == CAPACITY_ALERT_HOST


def test_invalid_settings_fall_back_to_safe_values(tmp_path) -> None:
    path = tmp_path / "slave_settings.json"
    path.write_text(
        '{"inventory_capacity_alert_level":"bad","inventory_capacity_threshold":99}',
        encoding="utf-8",
    )

    settings = load_slave_settings(path)

    assert not settings.inventory_capacity_alert_enabled
    assert settings.inventory_capacity_threshold == 20
    assert settings.relay_url == ""


def test_settings_dialog_shows_login_status_without_local_alert_controls() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SlaveSettingsDialog(SlaveSettings(device_id="slave-1", device_token="secret",
                                               account_username="trader", display_name="摆摊机1"))
    try:
        assert "trader" in dialog.binding_status_label.text()
        assert "摆摊机1" in dialog.binding_status_label.text()
        assert not hasattr(dialog, "capacity_alert_group")
        assert not hasattr(dialog, "capacity_threshold_spin")
        assert not hasattr(dialog, "settings")
    finally:
        dialog.close()
        app.processEvents()

def test_monitored_windows_round_trip_and_restore_plan(tmp_path: Path) -> None:
    settings = SlaveSettings(
        monitored_windows=[
            {"title": "梦幻西游 ONLINE - (江苏1区[秦淮风光] - 甲[123456])", "role_id": "123456",
             "server": "秦淮风光", "role_name": "甲"},
            {"title": "梦幻西游 ONLINE - (广东3区[罗浮山] - 乙[654321])", "role_id": "654321",
             "server": "罗浮山", "role_name": "乙"},
        ]
    )
    path = tmp_path / "slave_settings.json"
    save_slave_settings(settings, path)
    loaded = load_slave_settings(path)
    assert [item["role_id"] for item in loaded.monitored_windows] == ["123456", "654321"]

    windows = [(11, "999999", "别的窗口"), (12, "654321", "乙-改名了"), (13, "123456", "甲")]
    assert plan_window_restore(loaded.monitored_windows, windows) == [13, 12]
    assert plan_window_restore(loaded.monitored_windows, []) == []


def test_monitored_windows_are_limited_and_fall_back_to_title(tmp_path: Path) -> None:
    raw = {"monitored_windows": [{"role_id": str(index)} for index in range(12)]}
    settings = SlaveSettings.from_mapping(raw)
    assert len(settings.monitored_windows) == 8

    by_title = {"title": "窗口标题没有账号", "role_id": ""}
    assert matches_monitored_window(by_title, "", "窗口标题没有账号")
    assert not matches_monitored_window(by_title, "", "别的标题")
    assert matches_monitored_window({"role_id": "123456"}, "123456", "任意标题")

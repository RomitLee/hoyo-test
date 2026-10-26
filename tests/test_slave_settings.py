import json
import os

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


def test_settings_dialog_only_exposes_capacity_alert_and_login_status() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SlaveSettingsDialog(SlaveSettings())
    try:
        assert dialog.capacity_alert_group.exclusive()
        assert [dialog.capacity_alert_buttons[level].text() for level in (1, 2, 3)] == [
            "1 不提醒",
            "2 云端推送",
            "3 本机提醒",
        ]
        assert dialog.capacity_alert_group.checkedId() == CAPACITY_ALERT_NONE
        assert dialog.capacity_alert_buttons[CAPACITY_ALERT_NONE].isChecked()
        assert not dialog.capacity_threshold_spin.isEnabled()
        assert not hasattr(dialog, "pairing_code_edit")
        assert "未登录" in dialog.binding_status_label.text()
        assert not hasattr(dialog, "relay_url_edit")
        assert not hasattr(dialog, "device_token_edit")
        assert not hasattr(dialog, "minimize_alert_combo")
        assert not hasattr(dialog, "occlusion_alert_combo")
        assert not hasattr(dialog, "inventory_not_open_alert_combo")
        assert not hasattr(dialog, "inventory_blocked_alert_combo")

        dialog.capacity_alert_buttons[CAPACITY_ALERT_HOST].click()
        dialog.capacity_threshold_spin.setValue(3)
        assert dialog.capacity_alert_group.checkedId() == CAPACITY_ALERT_HOST
        assert not dialog.capacity_alert_buttons[CAPACITY_ALERT_NONE].isChecked()
        assert dialog.settings().inventory_capacity_alert_enabled
        assert dialog.settings().inventory_capacity_host_alert_enabled
        assert not dialog.settings().inventory_capacity_local_alert_enabled
        assert dialog.settings().inventory_capacity_threshold == 3
        assert dialog.settings().pairing_code == ""

        dialog.capacity_alert_buttons[CAPACITY_ALERT_LOCAL].click()
        assert dialog.capacity_alert_group.checkedId() == CAPACITY_ALERT_LOCAL
        assert not dialog.capacity_alert_buttons[CAPACITY_ALERT_HOST].isChecked()
        assert not dialog.settings().inventory_capacity_host_alert_enabled
        assert dialog.settings().inventory_capacity_local_alert_enabled
    finally:
        dialog.close()
        app.processEvents()

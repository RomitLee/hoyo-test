import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.settings_dialog import SlaveSettingsDialog
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
        "device_token",
        "installation_id",
        "pairing_code",
    }


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


def test_settings_dialog_only_exposes_capacity_alert_and_pairing() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SlaveSettingsDialog(SlaveSettings())
    try:
        assert dialog.capacity_alert_group.exclusive()
        assert [dialog.capacity_alert_buttons[level].text() for level in (1, 2, 3)] == [
            "1 不提醒",
            "2 主机推送",
            "3 本机提醒",
        ]
        assert dialog.capacity_alert_group.checkedId() == CAPACITY_ALERT_NONE
        assert dialog.capacity_alert_buttons[CAPACITY_ALERT_NONE].isChecked()
        assert not dialog.capacity_threshold_spin.isEnabled()
        assert dialog.pairing_code_edit.text() == ""
        assert "尚未绑定" in dialog.binding_status_label.text()
        assert not hasattr(dialog, "relay_url_edit")
        assert not hasattr(dialog, "device_token_edit")
        assert not hasattr(dialog, "minimize_alert_combo")
        assert not hasattr(dialog, "occlusion_alert_combo")
        assert not hasattr(dialog, "inventory_not_open_alert_combo")
        assert not hasattr(dialog, "inventory_blocked_alert_combo")

        dialog.capacity_alert_buttons[CAPACITY_ALERT_HOST].click()
        dialog.capacity_threshold_spin.setValue(3)
        dialog.pairing_code_edit.setText("123456")
        assert dialog.capacity_alert_group.checkedId() == CAPACITY_ALERT_HOST
        assert not dialog.capacity_alert_buttons[CAPACITY_ALERT_NONE].isChecked()
        assert dialog.settings().inventory_capacity_alert_enabled
        assert dialog.settings().inventory_capacity_host_alert_enabled
        assert not dialog.settings().inventory_capacity_local_alert_enabled
        assert dialog.settings().inventory_capacity_threshold == 3
        assert dialog.settings().pairing_code == "123456"

        dialog.capacity_alert_buttons[CAPACITY_ALERT_LOCAL].click()
        assert dialog.capacity_alert_group.checkedId() == CAPACITY_ALERT_LOCAL
        assert not dialog.capacity_alert_buttons[CAPACITY_ALERT_HOST].isChecked()
        assert not dialog.settings().inventory_capacity_host_alert_enabled
        assert dialog.settings().inventory_capacity_local_alert_enabled
    finally:
        dialog.close()
        app.processEvents()

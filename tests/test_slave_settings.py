import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.settings_dialog import SlaveSettingsDialog
from hoyo_analyzer.slave_settings import (
    ALERT_HOST,
    ALERT_LOCAL,
    ALERT_NONE,
    LOCAL_ALERT_POPUP,
    SlaveSettings,
    load_slave_settings,
    save_slave_settings,
)


def test_slave_settings_are_saved_and_loaded(tmp_path) -> None:
    path = tmp_path / "runtime" / "slave_settings.json"
    expected = SlaveSettings(
        minimize_alert=ALERT_NONE,
        occlusion_threshold_percent=30,
        occlusion_alert=ALERT_LOCAL,
        inventory_empty_threshold=3,
        inventory_alert=ALERT_LOCAL,
        inventory_not_open_alert=ALERT_NONE,
        inventory_blocked_alert=ALERT_LOCAL,
        local_alert_style=LOCAL_ALERT_POPUP,
        relay_url="https://relay.example.com",
        device_id="slave-01",
        device_token="device-secret-token",
    )

    save_slave_settings(expected, path)

    assert load_slave_settings(path) == expected


def test_invalid_slave_settings_fall_back_to_safe_values(tmp_path) -> None:
    path = tmp_path / "slave_settings.json"
    path.write_text(
        '{"minimize_alert":"bad","inventory_not_open_alert":"bad","inventory_blocked_alert":"bad",'
        '"occlusion_threshold_percent":999,"inventory_empty_threshold":-5}',
        encoding="utf-8",
    )

    settings = load_slave_settings(path)

    assert settings.minimize_alert == ALERT_LOCAL
    assert settings.inventory_not_open_alert == ALERT_LOCAL
    assert settings.inventory_blocked_alert == ALERT_LOCAL
    assert settings.occlusion_threshold_percent == 100
    assert settings.inventory_empty_threshold == 0
    assert settings.relay_url == ""


def test_settings_dialog_exposes_enabled_cloud_warning_and_credentials() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SlaveSettingsDialog(SlaveSettings())
    try:
        assert dialog.occlusion_threshold_spin.value() == 100
        assert dialog.inventory_threshold_spin.value() == 0
        assert dialog.relay_url_edit.text() == ""
        assert dialog.device_id_edit.text() == ""

        for combo in (
            dialog.minimize_alert_combo,
            dialog.occlusion_alert_combo,
            dialog.inventory_alert_combo,
            dialog.inventory_not_open_alert_combo,
            dialog.inventory_blocked_alert_combo,
        ):
            assert combo.itemData(2) == ALERT_HOST
            assert combo.model().item(2).isEnabled()
    finally:
        dialog.close()
        app.processEvents()

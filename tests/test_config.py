from hoyo_analyzer.config import load_config


def test_inventory_monitor_config_is_loaded(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[inventory_monitor]
enabled = false
confirm_frames = 5
alert_confirm_seconds = 6.5
closed_reminder_seconds = 90
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.inventory_monitor.enabled is False
    assert config.inventory_monitor.confirm_frames == 5
    assert config.inventory_monitor.alert_confirm_seconds == 6.5
    assert config.inventory_monitor.closed_reminder_seconds == 90

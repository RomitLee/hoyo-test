from hoyo_analyzer.master_settings import MasterSettings, load_master_settings, save_master_settings


def test_master_settings_round_trip(tmp_path) -> None:
    path = tmp_path / "runtime" / "master_settings.json"
    expected = MasterSettings("https://relay.example.com", "master-secret", 8)

    save_master_settings(expected, path)

    assert load_master_settings(path) == expected

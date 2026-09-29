import wave
from types import SimpleNamespace

from hoyo_analyzer import alarm_sound


def test_alarm_melody_is_written_as_a_real_wav(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(alarm_sound, "application_root", lambda: tmp_path)

    path = alarm_sound.ensure_alarm_sound_file()

    assert path.exists()
    assert path.read_bytes()[:4] == b"RIFF"
    with wave.open(str(path), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == alarm_sound.SAMPLE_RATE
        assert source.getnframes() > alarm_sound.SAMPLE_RATE


def test_packaged_custom_alarm_is_preferred(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(alarm_sound, "application_root", lambda: tmp_path)
    custom = tmp_path / alarm_sound.CUSTOM_ALARM_PATH
    alarm_sound._ensure_wav(custom)

    assert alarm_sound.ensure_alarm_sound_file() == custom


def test_alarm_uses_streaming_repeat_mode(tmp_path, monkeypatch) -> None:
    alarm_path = tmp_path / "alarm.wav"
    calls: list[object] = []
    state = {"initialized": False, "busy": False}

    class FakeMusic:
        @staticmethod
        def stop() -> None:
            calls.append("stop")
            state["busy"] = False

        @staticmethod
        def unload() -> None:
            calls.append("unload")

        @staticmethod
        def load(path: str) -> None:
            calls.append(("load", path))

        @staticmethod
        def set_volume(volume: float) -> None:
            calls.append(("volume", volume))

        @staticmethod
        def play(*, loops: int) -> None:
            calls.append(("play", loops))
            state["busy"] = True

        @staticmethod
        def get_busy() -> bool:
            return state["busy"]

    def initialize(**kwargs: object) -> None:
        calls.append(("init", kwargs))
        state["initialized"] = True

    def quit_player() -> None:
        calls.append("quit")
        state["initialized"] = False

    fake_mixer = SimpleNamespace(
        get_init=lambda: state["initialized"],
        init=initialize,
        music=FakeMusic(),
        quit=quit_player,
    )
    monkeypatch.setattr(alarm_sound, "ensure_alarm_sound_file", lambda: alarm_path)
    monkeypatch.setattr(alarm_sound.pygame, "mixer", fake_mixer)

    assert alarm_sound.play_alarm_loop() == alarm_path
    assert alarm_sound.alarm_playback_status() == "playing"
    alarm_sound.stop_alarm_loop()
    assert alarm_sound.alarm_playback_status() == "stopped"

    assert calls == [
        ("init", {"frequency": alarm_sound.SAMPLE_RATE, "size": -16, "channels": 2, "buffer": 1024}),
        ("load", str(alarm_path)),
        ("volume", 1.0),
        ("play", -1),
        "stop",
        "unload",
        "quit",
    ]

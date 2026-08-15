from pathlib import Path

import pyboy

from pokemon_agent.emulator.pyboy_env import POKEMON_RED_DMG_PALETTE, PyBoyEnvironment


def test_pyboy_environment_uses_color_palette_without_forcing_cgb(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePyBoy:
        def __init__(self, rom_path: str, **kwargs: object) -> None:
            captured["rom_path"] = rom_path
            captured["kwargs"] = kwargs

    monkeypatch.setattr(pyboy, "PyBoy", FakePyBoy)

    rom = Path("pokered.gb")
    PyBoyEnvironment(rom, window="null")

    assert captured["rom_path"] == str(rom)
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["color_palette"] == POKEMON_RED_DMG_PALETTE
    assert kwargs["sound_emulated"] is True
    assert kwargs["sound_volume"] == 100
    assert "cgb" not in kwargs


def test_pyboy_environment_clamps_sound_volume(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePyBoy:
        def __init__(self, rom_path: str, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

    monkeypatch.setattr(pyboy, "PyBoy", FakePyBoy)

    PyBoyEnvironment(Path("pokered.gb"), window="SDL2", sound_volume=150)

    assert captured["kwargs"]["sound_volume"] == 100
    assert captured["kwargs"]["sound_emulated"] is True


def test_pokemon_red_palette_contains_actual_color() -> None:
    channels = {
        ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)
        for color in POKEMON_RED_DMG_PALETTE
    }

    assert len(channels) == 4
    assert all(red != green or green != blue for red, green, blue in channels)


def test_pyboy_environment_qt_backend_uses_null_window_and_standalone_audio(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSound:
        pass

    class FakePyBoy:
        def __init__(self, rom_path: str, **kwargs: object) -> None:
            captured["kwargs"] = kwargs
            self.sound = FakeSound()

        def tick(self, frames: int, render: bool) -> bool:
            captured["tick"] = (frames, render)
            return True

        def stop(self, save: bool = False) -> None:
            captured["stopped"] = save

    class FakeAudioOutput:
        def __init__(self, sound: object, volume: int) -> None:
            captured["audio_init"] = (sound, volume)

        def queue_frame(self) -> None:
            captured["audio_queued"] = True

        def close(self) -> None:
            captured["audio_closed"] = True

    monkeypatch.setattr(pyboy, "PyBoy", FakePyBoy)
    monkeypatch.setattr("pokemon_agent.emulator.pyboy_env._SdlAudioOutput", FakeAudioOutput)

    env = PyBoyEnvironment(Path("pokered.gb"), window="qt", sound_volume=80)
    assert captured["kwargs"]["window"] == "null"
    assert captured["audio_init"][1] == 80

    assert env.tick(1, render=True) is True
    assert captured["audio_queued"] is True
    env.stop(save=False)
    assert captured["audio_closed"] is True

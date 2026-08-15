from __future__ import annotations

from pathlib import Path
from typing import Any

from pokemon_agent import app
from pokemon_agent.ui.control_panel import ControlCommand, QtStateControlPanel, _parse_buttons_array
from pokemon_agent.vision.capture import CaptureConfig
from tests.fakes import FakePokemonEnvironment


class FakeControlPanel:
    def __init__(self):
        self.screen_updates = 0
        self.closed = False
        self.commands: list[Any] = []
        self.move_results: list[dict[str, Any]] = []
        self.buttons_results: list[dict[str, Any]] = []

    def update_screen_image(self, image: object) -> None:
        self.screen_updates += 1

    def update_overlay_image(self, image: object) -> None:
        pass

    def update_ram_text(self, text: str) -> None:
        pass

    def update_game_area_text(self, text: str) -> None:
        pass

    def update_collision_text(self, text: str) -> None:
        pass

    def notify_move_result(self, result: dict[str, Any]) -> None:
        self.move_results.append(result)

    def notify_buttons_result(self, result: dict[str, Any]) -> None:
        self.buttons_results.append(dict(result))

    def notify_saved(self, path: Path) -> None:
        pass

    def notify_loaded(self, path: Path) -> None:
        pass

    def notify_error(self, message: str) -> None:
        pass

    def poll(self, state: object) -> list[Any]:
        commands = list(self.commands)
        self.commands.clear()
        return commands

    def close(self) -> None:
        self.closed = True


class FakeScrollBar:
    def __init__(self, value: int = 0, maximum: int = 100):
        self._value = value
        self._maximum = maximum

    def value(self) -> int:
        return self._value

    def maximum(self) -> int:
        return self._maximum

    def setValue(self, value: int) -> None:
        self._value = value


class FakeCursor:
    def __init__(self, position: int = 0):
        self._position = position

    def position(self) -> int:
        return self._position

    def setPosition(self, position: int) -> None:
        self._position = position


class FakePlainTextBox:
    def __init__(
        self,
        text: str,
        vertical_value: int = 0,
        horizontal_value: int = 0,
        vertical_maximum: int = 100,
        next_vertical_maximum: int | None = None,
        cursor_position: int = 0,
    ):
        self.text = text
        self.set_plain_text_calls = 0
        self.next_vertical_maximum = next_vertical_maximum
        self.vertical_scroll = FakeScrollBar(vertical_value, vertical_maximum)
        self.horizontal_scroll = FakeScrollBar(horizontal_value, 100)
        self.cursor = FakeCursor(cursor_position)

    def toPlainText(self) -> str:
        return self.text

    def verticalScrollBar(self) -> FakeScrollBar:
        return self.vertical_scroll

    def horizontalScrollBar(self) -> FakeScrollBar:
        return self.horizontal_scroll

    def textCursor(self) -> FakeCursor:
        return self.cursor

    def setTextCursor(self, cursor: FakeCursor) -> None:
        self.cursor = cursor

    def setPlainText(self, text: str) -> None:
        self.set_plain_text_calls += 1
        self.text = text
        self.vertical_scroll.setValue(0)
        self.horizontal_scroll.setValue(0)
        self.cursor = FakeCursor(0)
        if self.next_vertical_maximum is not None:
            self.vertical_scroll._maximum = self.next_vertical_maximum


def test_run_rom_updates_control_panel_screen(monkeypatch: Any, tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    control_panel = FakeControlPanel()
    monkeypatch.setattr(app, "PyBoyEnvironment", lambda rom_path, window: fake_env)

    app.run_rom(
        tmp_path / "pokered.gb",
        steps=2,
        window="null",
        render=True,
        capture_config=CaptureConfig(directory=tmp_path / "captures"),
        state_dir=tmp_path / "states",
        load_state=None,
        save_final=None,
        save_every=0,
        tick_frames=1,
        control_panel=control_panel,  # type: ignore[arg-type]
    )

    assert control_panel.screen_updates > 0
    assert control_panel.closed is True


def test_run_rom_unbounded_mode_stops_when_emulator_closes(monkeypatch: Any, tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    original_tick = fake_env.tick

    def closing_tick(frames: int = 1, render: bool = False) -> bool:
        original_tick(frames, render)
        return len(fake_env.ticks) < 3

    fake_env.tick = closing_tick  # type: ignore[method-assign]
    monkeypatch.setattr(app, "PyBoyEnvironment", lambda rom_path, window: fake_env)

    app.run_rom(
        tmp_path / "pokered.gb",
        steps=None,
        window="null",
        render=True,
        capture_config=CaptureConfig(directory=tmp_path / "captures"),
        state_dir=tmp_path / "states",
        load_state=None,
        save_final=None,
        save_every=0,
        tick_frames=1,
        emulation_speed=1,
    )

    assert len(fake_env.ticks) == 3
    assert fake_env.emulation_speed == 1
    assert fake_env.stopped is True


def test_run_rom_can_keep_separate_sdl_game_window_with_control_panel(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_env = FakePokemonEnvironment()
    control_panel = FakeControlPanel()
    opened_windows: list[str] = []

    def create_environment(rom_path: Path, window: str) -> FakePokemonEnvironment:
        opened_windows.append(window)
        return fake_env

    monkeypatch.setattr(app, "PyBoyEnvironment", create_environment)

    app.run_rom(
        tmp_path / "pokered.gb",
        steps=1,
        window="SDL2",
        render=True,
        capture_config=CaptureConfig(directory=tmp_path / "captures"),
        state_dir=tmp_path / "states",
        load_state=None,
        save_final=None,
        save_every=0,
        tick_frames=1,
        control_panel=control_panel,  # type: ignore[arg-type]
        emulation_speed=1,
        merge_sdl_into_control_panel=False,
    )

    assert opened_windows == ["SDL2"]
    assert fake_env.emulation_speed == 1


def test_run_rom_control_panel_move_command(monkeypatch: Any, tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    control_panel = FakeControlPanel()
    control_panel.commands.append(ControlCommand("move", target=(6, 6)))
    monkeypatch.setattr(app, "PyBoyEnvironment", lambda rom_path, window: fake_env)

    app.run_rom(
        tmp_path / "pokered.gb",
        steps=2,
        window="null",
        render=True,
        capture_config=CaptureConfig(directory=tmp_path / "captures"),
        state_dir=tmp_path / "states",
        load_state=None,
        save_final=None,
        save_every=0,
        tick_frames=1,
        control_panel=control_panel,  # type: ignore[arg-type]
    )

    assert ("right", 4) in fake_env.buttons
    assert fake_env.ticks == [(1, True), (1, True)]
    assert control_panel.move_results[0]["requested_world_cell"] == {"x": 6, "y": 6}
    assert control_panel.move_results[0]["stop_reason"] == "planned_path_exhausted"


def test_run_rom_control_panel_move_command_waits_between_buttons(monkeypatch: Any, tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    control_panel = FakeControlPanel()
    control_panel.commands.append(ControlCommand("move", target=(7, 6)))
    monkeypatch.setattr(app, "PyBoyEnvironment", lambda rom_path, window: fake_env)

    app.run_rom(
        tmp_path / "pokered.gb",
        steps=10,
        window="null",
        render=True,
        capture_config=CaptureConfig(directory=tmp_path / "captures"),
        state_dir=tmp_path / "states",
        load_state=None,
        save_final=None,
        save_every=0,
        tick_frames=1,
        control_panel=control_panel,  # type: ignore[arg-type]
    )

    assert fake_env.buttons == [("right", 4)]
    assert len(fake_env.ticks) == 10
    assert control_panel.move_results[0]["stop_reason"] == "queued_path"


def test_run_rom_control_panel_buttons_command_waits_between_buttons(monkeypatch: Any, tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    control_panel = FakeControlPanel()
    control_panel.commands.append(ControlCommand("buttons", buttons=("a", "b", "wait", "start")))
    monkeypatch.setattr(app, "PyBoyEnvironment", lambda rom_path, window: fake_env)

    app.run_rom(
        tmp_path / "pokered.gb",
        steps=55,
        window="null",
        render=True,
        capture_config=CaptureConfig(directory=tmp_path / "captures"),
        state_dir=tmp_path / "states",
        load_state=None,
        save_final=None,
        save_every=0,
        tick_frames=1,
        control_panel=control_panel,  # type: ignore[arg-type]
    )

    assert fake_env.buttons == [("a", 4), ("b", 4)]
    assert len(fake_env.ticks) == 55
    assert control_panel.buttons_results[0]["stop_reason"] == "queued_buttons"
    assert control_panel.buttons_results[-1]["steps_taken"] == 3


def test_parse_buttons_array_accepts_json_and_comma_text() -> None:
    assert _parse_buttons_array('["a", "wait", "start"]') == ["a", "wait", "start"]
    assert _parse_buttons_array("a, wait b") == ["a", "wait", "b"]


def test_control_panel_single_button_queues_game_button() -> None:
    panel = QtStateControlPanel.__new__(QtStateControlPanel)
    panel.commands = []
    panel.status_label = type("Status", (), {"setText": lambda self, text: setattr(self, "text", text)})()

    panel._queue_single_button("a")
    panel._queue_single_button("b")

    assert [command.buttons for command in panel.commands] == [("a",), ("b",)]


def test_control_panel_text_update_preserves_scroll_position() -> None:
    panel = QtStateControlPanel.__new__(QtStateControlPanel)
    text_box = FakePlainTextBox("old text", vertical_value=42, horizontal_value=7, cursor_position=4)

    panel._set_plain_text_preserving_view(text_box, "new text\nwith more detail")

    assert text_box.toPlainText() == "new text\nwith more detail"
    assert text_box.verticalScrollBar().value() == 42
    assert text_box.horizontalScrollBar().value() == 7
    assert text_box.textCursor().position() == 4


def test_control_panel_text_update_keeps_bottom_pinned() -> None:
    panel = QtStateControlPanel.__new__(QtStateControlPanel)
    text_box = FakePlainTextBox(
        "old text",
        vertical_value=100,
        vertical_maximum=100,
        next_vertical_maximum=160,
    )

    panel._set_plain_text_preserving_view(text_box, "new text\nwith more detail")

    assert text_box.verticalScrollBar().value() == 160

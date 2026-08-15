import base64
import threading
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from pokemon_agent.memory.world_state import GameMode
import pokemon_agent.session as session_module
from pokemon_agent.session import BUTTON_HOLD_FRAMES, INPUT_AFTER_FRAMES, PokemonSession

from tests.fakes import FakePokemonEnvironment, fake_session_paths


def test_start_with_control_ui_uses_windowless_qt_audio_backend(monkeypatch, tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    requested_windows: list[str] = []
    fake_panel = type("FakePanel", (), {"close": lambda self: None})()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: requested_windows.append(window) or fake_env,
    )
    monkeypatch.setattr(session, "_ensure_control_panel", lambda: setattr(session, "control_panel", fake_panel))
    monkeypatch.setattr(session, "_refresh_control_panel", lambda *args, **kwargs: None)

    session.start(window="SDL2", load_fixed=False, control_ui=True)

    assert requested_windows == ["qt"]
    assert session.window == "qt"
    session.stop(save_final=False)


def test_observe_returns_png_base64_and_screen_data(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    observation = session.observe()

    assert observation["screenshot"]["format"] == "png"
    assert observation["screenshot"]["width"] == 160
    assert observation["screenshot"]["height"] == 144
    assert base64.b64decode(observation["screenshot"]["base64"]).startswith(b"\x89PNG")
    assert observation["screenshot_overlay"]["format"] == "png"
    assert observation["screenshot_overlay"]["width"] == 640
    assert observation["screenshot_overlay"]["height"] == 576
    assert observation["screenshot_overlay"]["collision_truthy"] == "walkable"
    assert observation["screenshot_overlay"]["walk_cell_size"] == 2
    assert observation["screenshot_overlay"]["player_map_position"] == {"x": 5, "y": 6}
    assert "walk_cell_map_coordinates" in observation["screenshot_overlay"]["overlays"]
    assert "screen_tile_coordinates" not in observation["screenshot_overlay"]["overlays"]
    assert "floor(screen_tile_x / 2)" in observation["screenshot_overlay"]["coordinate_formula"]
    overlay_bytes = base64.b64decode(observation["screenshot_overlay"]["base64"])
    assert overlay_bytes.startswith(b"\x89PNG")
    with Image.open(BytesIO(overlay_bytes)) as overlay:
        assert overlay.size == (640, 576)
    assert len(observation["game_area"]) == 18
    assert len(observation["game_area_collision"]) == 18
    assert len(observation["walk_area_collision"]) == 9
    assert len(observation["walk_area_collision"][0]) == 10
    assert all(value == 1 for row in observation["walk_area_collision"] for value in row)
    assert len(observation["visible_world_cells"]) == 9
    assert len(observation["visible_world_cells"][0]) == 10
    assert observation["visible_world_cells"][4][4] == {"x": 5, "y": 6, "walkable": True}
    assert {"direction": "right", "x": 6, "y": 6} in observation["safe_neighbor_world_cells"]
    assert observation["state_events"][0]["type"] == "initial_observation"
    assert observation["state"]["events"] == observation["state_events"]
    assert observation["state"]["dialog"]["open"] is False
    assert "battle" not in observation["state"]
    assert observation["state"]["map"]["name"] == "Pallet Town"
    assert observation["state"]["position_detail"]["tile"] == {"x": 5, "y": 6}
    assert observation["state"]["counts"]["party"] == 0


def test_observe_only_sends_battle_opponent_during_battle(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(paths=fake_session_paths(tmp_path), env_factory=lambda rom, window: fake_env)
    session.start(load_fixed=False)

    assert "battle" not in session.observe()["state"]

    fake_env.memory[0xD057] = 1
    fake_env.memory[0xCFE5] = 0xA5
    fake_env.memory[0xCFE7] = 9
    fake_env.memory[0xCFE9] = 0x08
    fake_env.memory[0xCFEA] = 0x00
    fake_env.memory[0xCFEB] = 0x00
    fake_env.memory[0xCFF3] = 4
    fake_env.memory[0xCFF5] = 15
    _wait_for_next_frame(session)

    battle = session.observe()["state"]["battle"]
    assert battle["opponent"] == {
        "species": "Rattata",
        "level": 4,
        "hp": 9,
        "max_hp": 15,
        "status": "Poison",
        "types": ["Normal"],
    }
    assert "moves" not in battle["opponent"]
    assert "move_pp" not in battle["opponent"]


def test_observe_tracks_ram_derived_state_events(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    session.observe()

    _write_dialog_tiles(fake_env.memory, "HI")
    _wait_for_next_frame(session)
    opened = session.observe()
    assert {"type": "dialog_opened", "text": "HI"} in opened["state_events"]
    assert "event_flags_changed" not in {event["type"] for event in opened["state_events"]}
    assert opened["state"]["dialog"]["open"] is True
    assert opened["state"]["dialog"]["box_detected"] is True

    _write_dialog_tiles(fake_env.memory, "BY")
    _wait_for_next_frame(session)
    changed = session.observe()
    assert {
        "type": "dialog_text_changed",
        "from": "HI",
        "to": "BY",
    } in changed["state_events"]

    _clear_dialog_tiles(fake_env.memory)
    _wait_for_next_frame(session)
    closed = session.observe()
    assert {"type": "dialog_closed", "previous_text": "BY"} in closed["state_events"]
    assert closed["state"]["dialog"]["open"] is False


def test_observe_coalesces_progressive_dialog_typing_events(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    session.observe()

    for text in ("W", "WA", "WAN", "WANT"):
        _write_dialog_tiles(fake_env.memory, text)
        _wait_for_next_frame(session)
    opened = session.observe()

    assert {"type": "dialog_opened", "text": "WANT"} in opened["state_events"]
    assert not any(event["type"] == "dialog_text_changed" for event in opened["state_events"])

    for text in ("N", "NE", "NEXT"):
        _write_dialog_tiles(fake_env.memory, text)
        _wait_for_next_frame(session)
    changed = session.observe()

    assert changed["state_events"] == [
        {"type": "dialog_text_changed", "from": "WANT", "to": "NEXT"}
    ]


def test_move_to_world_cell_executes_dijkstra_direction(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_world_cell(6, 6)

    assert result["requested_world_cell"] == {"x": 6, "y": 6}
    assert result["resolved_world_cell"] == {"x": 6, "y": 6}
    assert result["executed_actions"][0]["button"] == "right"
    assert result["steps_taken"] == 1
    assert result["stop_reason"] == "target_reached"
    assert "requested_walk_cell" not in result
    assert any(render for _, render in fake_env.ticks)


def test_move_waits_for_realtime_settle_between_direction_inputs(tmp_path: Path, monkeypatch) -> None:
    class TickSpacingEnvironment(FakePokemonEnvironment):
        def __init__(self):
            super().__init__()
            self.button_tick_counts: list[int] = []

        def button(self, button: str, frames: int = 1) -> None:
            self.button_tick_counts.append(len(self.ticks))
            super().button(button, frames)

    fake_env = TickSpacingEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_world_cell(7, 6)

    assert [action["button"] for action in result["executed_actions"]] == ["right", "right"]
    assert (
        fake_env.button_tick_counts[1] - fake_env.button_tick_counts[0]
        >= BUTTON_HOLD_FRAMES + INPUT_AFTER_FRAMES
    )
    assert result["stop_reason"] == "target_reached"


def test_button_array_waits_hold_and_after_frames_between_inputs(tmp_path: Path) -> None:
    class TickSpacingEnvironment(FakePokemonEnvironment):
        def __init__(self):
            super().__init__()
            self.button_tick_counts: list[int] = []

        def button(self, button: str, frames: int = 1) -> None:
            self.button_tick_counts.append(len(self.ticks))
            super().button(button, frames)

    fake_env = TickSpacingEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.press_buttons(["a", "start"])

    assert result["executed_actions"] == [{"button": "a"}, {"button": "start"}]
    assert (
        fake_env.button_tick_counts[1] - fake_env.button_tick_counts[0]
        >= BUTTON_HOLD_FRAMES + INPUT_AFTER_FRAMES
    )


def test_move_to_world_cell_replans_until_out_of_view_target_is_reached(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_world_cell(0, 6)

    assert result["requested_world_cell"] == {"x": 0, "y": 6}
    assert result["target_out_of_visible_area"] is True
    assert result["resolved_world_cell"] == {"x": 0, "y": 6}
    assert [action["button"] for action in result["executed_actions"]] == [
        "left",
        "left",
        "left",
        "left",
        "left",
    ]
    assert result["requested_target_reached"] is True
    assert result["navigation_replans"] == 1
    assert result["stop_reason"] == "target_reached"


def test_remote_world_move_refreshes_collision_and_replans_across_multiple_screens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(session_module, "INPUT_AFTER_FRAMES", 0)

    class TrackingEnvironment(FakePokemonEnvironment):
        def __init__(self) -> None:
            super().__init__()
            self.collision_reads = 0

        def game_area_collision(self):
            self.collision_reads += 1
            return super().game_area_collision()

    fake_env = TrackingEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_world_cell(20, 6)

    assert result["requested_target_reached"] is True
    assert result["resolved_world_cell"] == {"x": 20, "y": 6}
    assert result["navigation_replans"] == 2
    assert len(result["navigation_segments"]) == 3
    assert [action["button"] for action in result["executed_actions"]] == ["right"] * 15
    assert result["planned_path"][0] == {"x": 5, "y": 6}
    assert result["planned_path"][-1] == {"x": 20, "y": 6}
    assert fake_env.collision_reads >= 3
    assert result["stop_reason"] == "target_reached"


def test_remote_world_move_stops_when_the_map_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "INPUT_AFTER_FRAMES", 0)

    class WarpEnvironment(FakePokemonEnvironment):
        def button(self, button: str, frames: int = 1) -> None:
            super().button(button, frames)
            if button == "right" and self.memory[0xD362] >= 7:
                self.memory[0xD35E] = 1

    fake_env = WarpEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_world_cell(20, 6)

    assert result["requested_target_reached"] is False
    assert [action["button"] for action in result["executed_actions"]] == ["right", "right"]
    assert result["after_observation"]["state"]["map_id"] == 1
    assert result["stop_reason"] == "interrupted_map_change"


def test_out_of_view_target_does_not_report_current_cell_as_reached(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    fake_env.collision = [[0 for _ in range(20)] for _ in range(18)]
    for y in (8, 9):
        for x in (8, 9):
            fake_env.collision[y][x] = 1
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_world_cell(0, 6)

    assert result["executed_actions"] == []
    assert result["resolved_world_cell"] == {"x": 5, "y": 6}
    assert result["stop_reason"] == "no_path"

    visible_result = session.move_to_world_cell(6, 6)
    assert visible_result["executed_actions"] == []
    assert visible_result["requested_target_reached"] is False
    assert visible_result["resolved_target_reached"] is True
    assert visible_result["stop_reason"] == "target_reached"


def test_occupied_world_target_succeeds_at_nearest_reachable_cell_even_when_dialog_opens(
    tmp_path: Path,
) -> None:
    fake_env = FakePokemonEnvironment()
    fake_env.collision = [[0 for _ in range(20)] for _ in range(18)]
    for walk_x, walk_y in ((4, 4), (4, 3)):
        for tile_y in (walk_y * 2, walk_y * 2 + 1):
            for tile_x in (walk_x * 2, walk_x * 2 + 1):
                fake_env.collision[tile_y][tile_x] = 1
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    original_read = session.reader.read

    def read_with_arrival_dialog(memory):
        state = original_read(memory)
        if state.position is not None and state.position.y == 5:
            state.dialog_open = True
            state.mode = GameMode.TALK
        return state

    session.reader.read = read_with_arrival_dialog  # type: ignore[method-assign]
    result = session.move_to_world_cell(5, 4)

    assert result["requested_world_cell"] == {"x": 5, "y": 4}
    assert result["resolved_world_cell"] == {"x": 5, "y": 5}
    assert result["requested_target_reached"] is False
    assert result["resolved_target_reached"] is True
    assert result["stop_reason"] == "target_reached"


def test_navigation_learns_a_blocked_directional_edge_and_replans(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(session_module, "MOVE_STEP_TIMEOUT_SECONDS", 0.05)
    class DirectionalBlockEnvironment(FakePokemonEnvironment):
        def button(self, button: str, frames: int = 1) -> None:
            if button == "right" and self.memory[0xD362] == 5 and self.memory[0xD361] == 6:
                self.buttons.append((button, frames))
                return
            super().button(button, frames)

    fake_env = DirectionalBlockEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)

    first = session.move_to_world_cell(6, 5)
    second = session.move_to_world_cell(6, 5)

    assert first["stop_reason"] == "movement_blocked"
    assert [action["button"] for action in first["executed_actions"]] == ["right"]
    assert [action["button"] for action in second["executed_actions"]] == ["up", "right"]
    assert second["stop_reason"] == "target_reached"


def test_navigation_does_not_learn_an_edge_while_controls_are_locked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(session_module, "MOVE_STEP_TIMEOUT_SECONDS", 0.05)
    class LockedEnvironment(FakePokemonEnvironment):
        def button(self, button: str, frames: int = 1) -> None:
            if self.memory[0xCD6B]:
                self.buttons.append((button, frames))
                return
            super().button(button, frames)

    fake_env = LockedEnvironment()
    fake_env.memory[0xCD6B] = 0xF0
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)

    first = session.move_to_world_cell(6, 6)
    fake_env.memory[0xCD6B] = 0
    second = session.move_to_world_cell(6, 6)

    assert first["stop_reason"] == "controls_locked"
    assert [action["button"] for action in second["executed_actions"]] == ["right"]
    assert second["stop_reason"] == "target_reached"


def test_move_reports_target_reached_when_script_locks_controls_after_position_change(
    tmp_path: Path,
) -> None:
    class ScriptedArrivalEnvironment(FakePokemonEnvironment):
        def button(self, button: str, frames: int = 1) -> None:
            super().button(button, frames)
            self.memory[0xCD6B] = 0xF0

    fake_env = ScriptedArrivalEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)

    result = session.move_to_world_cell(6, 6)

    assert result["after_observation"]["state"]["position"] == {"x": 6, "y": 6}
    assert result["after_observation"]["state"]["raw"]["controls_locked"] is True
    assert result["stop_reason"] == "target_reached"


def test_press_buttons_and_wait_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.01)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    pressed = session.press_buttons(["a", "wait", "b"])
    waited = session.wait()

    assert [action["button"] for action in pressed["executed_actions"]] == ["a", "wait", "b"]
    assert pressed["executed_actions"][1] == {"button": "wait"}
    assert fake_env.buttons[-2:] == [("a", BUTTON_HOLD_FRAMES), ("b", BUTTON_HOLD_FRAMES)]
    assert waited["waited"] is True
    assert waited["stop_reason"] == "wait_complete"
    assert all("frames" not in action and "after_frames" not in action for action in pressed["executed_actions"])
    assert session.realtime_tick_status()["enabled"] is True


def test_session_observation_updates_dynamic_world_map(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    observation = session.observe()

    assert observation["world_map"]["map_name"] == "Pallet Town"
    assert observation["world_map"]["known_tiles"] > 0
    assert observation["world_map"]["visited_tiles"] == 1
    assert observation["world_map"]["nearest_screen_tile"] is not None


def test_realtime_ticker_advances_one_frame_at_a_time_without_pump(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    session.set_realtime_ticking(enabled=True, fps=60)
    deadline = time.monotonic() + 0.5
    while session.frame_index == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    frame_before_pump = session.frame_index
    result = session.pump_realtime()

    assert result["enabled"] is True
    assert frame_before_pump > 0
    assert result["frame_index"] >= frame_before_pump
    assert result["frames_ticked"] >= frame_before_pump
    assert fake_env.ticks
    assert all(frames == 1 for frames, _ in fake_env.ticks)


def test_observation_listener_receives_cached_frames_without_consuming_observe_events(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    received: list[dict] = []
    listener = received.append

    session.start(load_fixed=False)
    session.add_observation_listener(listener)
    try:
        deadline = time.monotonic() + 0.5
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert received
        observation = session.observe()
        assert received[-1]["frame_index"] > 0
        assert observation["frame_index"] >= received[-1]["frame_index"]
    finally:
        session.remove_observation_listener(listener)
        session.stop()


def test_move_to_world_cell_stops_when_dialog_is_open(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    original_read = session.reader.read

    def read_with_dialog(memory):
        state = original_read(memory)
        state.dialog_open = True
        return state

    session.reader.read = read_with_dialog  # type: ignore[method-assign]
    result = session.move_to_world_cell(6, 6)

    assert result["executed_actions"] == []
    assert result["stop_reason"] == "interrupted_dialog"


@pytest.mark.parametrize(
    ("mode", "in_battle", "expected_reason"),
    [
        (GameMode.BATTLE, True, "interrupted_battle"),
        (GameMode.INVENTORY, False, "interrupted_menu"),
    ],
)
def test_move_to_world_cell_stops_for_battle_or_menu(
    tmp_path: Path,
    mode: GameMode,
    in_battle: bool,
    expected_reason: str,
) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    original_read = session.reader.read

    def read_interrupted(memory):
        state = original_read(memory)
        state.mode = mode
        state.in_battle = in_battle
        return state

    session.reader.read = read_interrupted  # type: ignore[method-assign]
    result = session.move_to_world_cell(6, 6)

    assert result["executed_actions"] == []
    assert result["stop_reason"] == expected_reason


def test_actions_never_tick_from_the_calling_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.01)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    callers: list[str] = []
    original_tick = session._tick

    def recording_tick(frames: int, render: bool = False) -> bool:
        callers.append(threading.current_thread().name)
        return original_tick(frames, render=render)

    monkeypatch.setattr(session, "_tick", recording_tick)
    session.press_buttons(["a"])
    session.wait()
    session.move_to_world_cell(6, 6)

    assert callers
    assert set(callers) == {"pokemon-realtime-ticker"}


def test_button_commands_are_applied_by_emulator_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.01)

    class ThreadRecordingEnvironment(FakePokemonEnvironment):
        def __init__(self):
            super().__init__()
            self.button_threads: list[str] = []

        def button(self, button: str, frames: int = 1) -> None:
            self.button_threads.append(threading.current_thread().name)
            super().button(button, frames)

    fake_env = ThreadRecordingEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)

    session.press_buttons(["a", "b"])

    assert fake_env.button_threads == ["pokemon-realtime-ticker", "pokemon-realtime-ticker"]


def test_observe_returns_cached_snapshot_while_visual_capture_is_blocked(tmp_path: Path) -> None:
    class SlowVisualEnvironment(FakePokemonEnvironment):
        def __init__(self):
            super().__init__()
            self.block_visual = False
            self.capture_started = threading.Event()
            self.release_capture = threading.Event()

        def screen_image(self):
            if self.block_visual:
                self.capture_started.set()
                self.release_capture.wait(timeout=1.0)
            return super().screen_image()

    fake_env = SlowVisualEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    initial = session.observe(refresh_control_panel=False)
    fake_env.block_visual = True
    session._next_visual_snapshot_at = 0.0
    assert fake_env.capture_started.wait(timeout=0.5)

    started_at = time.monotonic()
    cached = session.observe(refresh_control_panel=False)
    elapsed = time.monotonic() - started_at
    fake_env.release_capture.set()

    assert elapsed < 0.05
    assert cached["screenshot"]["base64"] == initial["screenshot"]["base64"]


def test_concurrent_actions_are_fifo_serialized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.02)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    original_wait = session._wait_realtime_frames
    active_waits = 0
    max_active_waits = 0
    counter_lock = threading.Lock()

    def tracked_wait(frames: int, *, start_frame: int) -> bool:
        nonlocal active_waits, max_active_waits
        with counter_lock:
            active_waits += 1
            max_active_waits = max(max_active_waits, active_waits)
        try:
            return original_wait(frames, start_frame=start_frame)
        finally:
            with counter_lock:
                active_waits -= 1

    monkeypatch.setattr(session, "_wait_realtime_frames", tracked_wait)
    first = threading.Thread(target=lambda: session.press_buttons(["a"]), name="first-action")
    second = threading.Thread(target=lambda: session.press_buttons(["b"]), name="second-action")
    first.start()
    time.sleep(0.005)
    second.start()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert [button for button, _ in fake_env.buttons] == ["a", "b"]
    assert max_active_waits == 1


def test_stop_wakes_an_action_waiting_for_realtime_ticker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 10.0)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    result_holder: list[dict] = []
    action = threading.Thread(target=lambda: result_holder.append(session.press_buttons(["a"])))
    action.start()
    deadline = time.monotonic() + 0.5
    while not fake_env.buttons and time.monotonic() < deadline:
        time.sleep(0.005)

    stop_result = session.stop()
    action.join(timeout=1.0)

    assert stop_result["stopped"] is True
    assert not action.is_alive()
    assert result_holder[0]["stop_reason"] == "realtime_ticker_stopped"


def test_ticker_failure_wakes_waiting_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 10.0)

    class FailingTickerEnvironment(FakePokemonEnvironment):
        def tick(self, frames: int = 1, render: bool = False) -> bool:
            raise RuntimeError("ticker failed")

    fake_env = FailingTickerEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)

    result = session.press_buttons(["a"])

    assert result["stop_reason"] == "realtime_ticker_stopped"
    assert "ticker failed" in str(session.realtime_tick_status()["ticker_error"])


def test_load_state_waits_for_active_action(tmp_path: Path, monkeypatch) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    session.start(load_fixed=False)
    wait_entered = threading.Event()
    release_wait = threading.Event()

    def controlled_wait(frames: int, *, start_frame: int) -> bool:
        wait_entered.set()
        return release_wait.wait(timeout=1.0)

    monkeypatch.setattr(session, "_wait_realtime_frames", controlled_wait)
    action = threading.Thread(target=lambda: session.press_buttons(["a"]))
    loader = threading.Thread(target=lambda: session.load_state(kind="fixed"))
    action.start()
    assert wait_entered.wait(timeout=0.5)
    loader.start()
    time.sleep(0.02)

    assert loader.is_alive()
    assert fake_env.loaded_paths == []

    release_wait.set()
    action.join(timeout=1.0)
    loader.join(timeout=1.0)

    assert not action.is_alive()
    assert not loader.is_alive()
    assert fake_env.loaded_paths == [session.paths.fixed_state]


def _write_dialog_tiles(memory, text: str) -> None:
    _clear_dialog_tiles(memory)
    memory[0xC3A0] = 0x7C
    for offset, char in enumerate(text, start=1):
        memory[0xC3A0 + offset] = ord(char.upper()) - ord("A") + 0x80
    memory[0xC3A0 + len(text) + 1] = 0x7C


def _clear_dialog_tiles(memory) -> None:
    for offset in range(16):
        memory[0xC3A0 + offset] = 0


def _wait_for_next_frame(session: PokemonSession, timeout: float = 0.5) -> None:
    with session._snapshot_condition:
        start_frame = int((session._latest_observation or {}).get("frame_index", -1))
    assert session._wait_for_snapshot_after(start_frame, timeout=timeout)

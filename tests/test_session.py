import base64
from pathlib import Path

from pokemon_agent.session import PokemonSession

from tests.fakes import FakePokemonEnvironment, fake_session_paths


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
    assert len(observation["game_area"]) == 18
    assert len(observation["game_area_collision"]) == 18


def test_move_to_screen_tile_executes_dijkstra_direction(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    result = session.move_to_screen_tile(10, 8, max_steps=8)

    assert result["requested_target"]["screen_tile"] == {"x": 10, "y": 8}
    assert result["resolved_target"]["walk_cell"] == {"x": 5, "y": 4}
    assert result["executed_actions"][0]["button"] == "right"
    assert result["steps_taken"] == 1
    assert result["stop_reason"] == "target_reached"
    assert any(render for _, render in fake_env.ticks)


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


def test_realtime_tick_pump_advances_frames_without_planner_action(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )

    session.start(load_fixed=False)
    session.set_realtime_ticking(enabled=True, fps=60, max_frames_per_pump=4)
    session._last_realtime_tick_at = 10.0

    result = session.pump_realtime(now=10.5)

    assert result["enabled"] is True
    assert result["frames_ticked"] == 4
    assert result["frame_index"] == 4
    assert fake_env.ticks[-1] == (4, True)


def test_move_to_screen_tile_stops_when_dialog_is_open(tmp_path: Path) -> None:
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
    result = session.move_to_screen_tile(10, 8, max_steps=8)

    assert result["executed_actions"] == []
    assert result["stop_reason"] == "interrupted_dialog"

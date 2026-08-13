from pathlib import Path

from pokemon_agent import mcp_server
from pokemon_agent.session import PokemonSession

from tests.fakes import FakePokemonEnvironment, fake_session_paths


def test_mcp_move_to_screen_tile_uses_current_session(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    mcp_server.start_session(load_fixed=False, control_ui=False)
    result = mcp_server.move_to_screen_tile(10, 8, max_steps=8)

    assert result["requested_target"]["screen_tile"] == {"x": 10, "y": 8}
    assert result["executed_actions"][0]["button"] == "right"
    assert "before_observation" in result
    assert "after_observation" in result

    log_result = mcp_server.recent_mcp_commands(limit=20)
    commands = log_result["commands"]
    tools = [entry["tool"] for entry in commands]
    move_entries = [entry for entry in commands if entry["tool"] == "move_to_screen_tile"]

    assert "start_session" in tools
    assert move_entries[-1]["status"] == "ok"
    assert move_entries[-1]["args"]["target_x"] == 10
    assert "target_reached" in move_entries[-1]["result_summary"]
    assert "MCP Log" not in log_result["text"]


def test_mcp_realtime_tick_controls_current_session(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    mcp_server.start_session(load_fixed=False, control_ui=False)
    result = mcp_server.set_realtime_ticks(enabled=True, fps=30, max_frames_per_pump=2)
    status = mcp_server.realtime_tick_status()

    assert result["enabled"] is True
    assert result["fps"] == 30
    assert status["enabled"] is True
    assert status["max_frames_per_pump"] == 2

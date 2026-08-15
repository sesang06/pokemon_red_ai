import inspect
from pathlib import Path
from types import SimpleNamespace

from pokemon_agent import mcp_server
import pokemon_agent.session as session_module
from pokemon_agent.session import PokemonSession

from tests.fakes import FakePokemonEnvironment, fake_session_paths


def test_mcp_move_to_world_cell_uses_map_coordinates(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    mcp_server.start_session(load_fixed=False, control_ui=False)
    result = mcp_server.move_to_world_cell(6, 6)
    log_result = mcp_server.recent_mcp_commands(limit=20)

    assert result["requested_world_cell"] == {"x": 6, "y": 6}
    assert result["resolved_world_cell"] == {"x": 6, "y": 6}
    assert "requested_walk_cell" not in result
    assert result["executed_actions"] == [{"button": "right"}]
    assert "before_observation" in result
    assert "after_observation" in result
    move_entries = [entry for entry in log_result["commands"] if entry["tool"] == "move_to_world_cell"]
    assert move_entries[-1]["args"] == {"target_x": 6, "target_y": 6}
    assert "target_reached" in move_entries[-1]["result_summary"]


def test_mcp_press_buttons_and_wait_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.01)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    mcp_server.start_session(load_fixed=False, control_ui=False)
    pressed = mcp_server.press_buttons(["a", "wait", "b"])
    waited = mcp_server.wait()
    log_result = mcp_server.recent_mcp_commands(limit=20)

    assert pressed["executed_actions"] == [{"button": "a"}, {"button": "wait"}, {"button": "b"}]
    assert waited["waited"] is True
    assert waited["stop_reason"] == "wait_complete"
    assert "press_buttons" in [entry["tool"] for entry in log_result["commands"]]
    wait_entries = [entry for entry in log_result["commands"] if entry["tool"] == "wait"]
    assert "elapsed_ms=" in wait_entries[-1]["result_summary"]


def test_mcp_action_schema_has_no_legacy_parameters_or_walk_tool() -> None:
    assert list(inspect.signature(mcp_server.press_buttons).parameters) == ["buttons"]
    assert list(inspect.signature(mcp_server.wait).parameters) == []
    assert list(inspect.signature(mcp_server.move_to_world_cell).parameters) == ["target_x", "target_y"]
    assert not hasattr(mcp_server, "move_to_walk_cell")


def test_mcp_realtime_tick_controls_current_session(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    mcp_server.start_session(load_fixed=False, control_ui=False)
    result = mcp_server.set_realtime_ticks(enabled=True, fps=30)
    status = mcp_server.realtime_tick_status()

    assert result["enabled"] is True
    assert result["fps"] == 30
    assert status["enabled"] is True
    assert status["snapshot_hz"] == 30


def test_mcp_dashboard_bridge_forwards_trace_runtime_and_memory(monkeypatch) -> None:
    class FakeHub:
        def __init__(self) -> None:
            self.traces = []
            self.runtime = []
            self.memory = []

        def publish_trace(self, trace):
            self.traces.append(trace)

        def publish_runtime(self, state, *, phase):
            self.runtime.append((state, phase))

        def publish_memory_snapshot(self, items):
            self.memory.append((items, None))

        def publish_memory_activity(self, items, activity):
            self.memory.append((items, activity))

    hub = FakeHub()
    monkeypatch.setattr(mcp_server, "_DASHBOARD", SimpleNamespace(hub=hub))
    items = {"map:Pallet Town": {"value": "known exit"}}

    assert mcp_server.dashboard_publish_trace(
        {"phase": "planning_thinking", "thinking_summary": "Head north."}
    )["published"] is True
    assert mcp_server.dashboard_publish_runtime({"step_count": 2}, "planned")["published"] is True
    assert mcp_server.dashboard_publish_memory(items)["published"] is True
    assert mcp_server.dashboard_publish_memory(
        items,
        {"tool": "search_memory", "keys": ["map:Pallet Town"]},
    )["published"] is True

    assert hub.traces[0]["thinking_summary"] == "Head north."
    assert hub.runtime == [({"step_count": 2}, "planned")]
    assert hub.memory[0] == (items, None)
    assert hub.memory[1][1]["keys"] == ["map:Pallet Town"]

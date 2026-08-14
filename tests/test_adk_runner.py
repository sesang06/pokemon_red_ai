from __future__ import annotations

from pokemon_agent.adk_agent.agents.planner.agent import DEFAULT_ADK_MODEL
from pokemon_agent.adk_agent.runner import parse_args


def test_adk_runner_defaults_to_live_vision_loop() -> None:
    args = parse_args([])

    assert args.steps == 100
    assert args.window == "null"
    assert args.control_ui is True
    assert args.realtime_ticks is True
    assert args.realtime_fps == 60.0
    assert args.ui_refresh_hz == 30.0
    assert args.adk_model == DEFAULT_ADK_MODEL
    assert args.adk_vision is True
    assert args.adk_thinking_budget == -1
    assert args.agent_trace is True
    assert args.dashboard is True
    assert args.dashboard_host == "127.0.0.1"
    assert args.dashboard_port == 8765
    assert str(args.action_log_dir) == "logs\\actions"
    assert args.adk_session_db.name == "adk_sessions.db"
    assert args.runtime_state_path.name == "adk_runtime_state.json"


def test_adk_runner_defaults_can_be_disabled() -> None:
    args = parse_args(
        [
            "--no-control-ui",
            "--no-realtime-ticks",
            "--no-adk-model",
            "--no-adk-vision",
            "--no-adk-thinking",
            "--no-agent-trace",
            "--no-dashboard",
            "--no-action-log",
        ]
    )

    assert args.control_ui is False
    assert args.realtime_ticks is False
    assert args.adk_model is None
    assert args.adk_vision is False
    assert args.adk_thinking_budget is None
    assert args.agent_trace is False
    assert args.dashboard is False
    assert args.action_log_dir is None

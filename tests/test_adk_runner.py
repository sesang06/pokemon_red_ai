from __future__ import annotations

from pokemon_agent.adk_agent.adk_planner import DEFAULT_ADK_MODEL
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


def test_adk_runner_defaults_can_be_disabled() -> None:
    args = parse_args(["--no-control-ui", "--no-realtime-ticks", "--no-adk-model", "--no-adk-vision"])

    assert args.control_ui is False
    assert args.realtime_ticks is False
    assert args.adk_model is None
    assert args.adk_vision is False

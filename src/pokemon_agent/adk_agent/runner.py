from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from pokemon_agent.adk_agent.adk_planner import DEFAULT_ADK_MODEL, GoogleAdkPlanner
from pokemon_agent.adk_agent.client import InProcessPokemonMcpClient
from pokemon_agent.adk_agent.loop import PokemonAdkLoop

DEFAULT_STEPS = 100
DEFAULT_CONTROL_UI = True
DEFAULT_REALTIME_TICKS = True
DEFAULT_ADK_VISION = True


def load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return

    project_root = Path(__file__).resolve().parents[3]
    load_dotenv(project_root / ".env")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MCP-backed Google ADK Pokemon Red agent loop.")
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS, help="Number of safe-loop action cycles.")
    parser.add_argument("--window", default="null", help="PyBoy window backend, for example null or SDL2.")
    parser.add_argument("--objective", default="safe_loop", help="Agent objective label.")
    parser.add_argument("--checkpoint-every", type=int, default=10, help="Save PyBoy state every N loop actions.")
    parser.add_argument("--no-load-fixed", action="store_true", help="Start without loading states/fixed_start.state.")
    parser.add_argument(
        "--control-ui",
        dest="control_ui",
        action="store_true",
        default=DEFAULT_CONTROL_UI,
        help="Open the PySide6 control panel while the loop runs.",
    )
    parser.add_argument("--no-control-ui", dest="control_ui", action="store_false", help="Run without the PySide6 control panel.")
    parser.add_argument("--save-final", action="store_true", help="Save states/last.state before stopping.")
    parser.add_argument(
        "--realtime-ticks",
        dest="realtime_ticks",
        action="store_true",
        default=DEFAULT_REALTIME_TICKS,
        help="Tick the emulator continuously while the planner is waiting.",
    )
    parser.add_argument("--no-realtime-ticks", dest="realtime_ticks", action="store_false", help="Disable realtime ticking.")
    parser.add_argument("--realtime-fps", type=float, default=60.0, help="Game frames per second for --realtime-ticks.")
    parser.add_argument("--ui-refresh-hz", type=float, default=30.0, help="How often to pump realtime ticks and the control panel.")
    parser.add_argument(
        "--adk-model",
        default=os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
        help=f"Enable Google ADK LLM planning with this model, for example {DEFAULT_ADK_MODEL}.",
    )
    parser.add_argument("--no-adk-model", dest="adk_model", action="store_const", const=None, help="Use the built-in rule planner only.")
    parser.add_argument(
        "--adk-vision",
        dest="adk_vision",
        action="store_true",
        default=DEFAULT_ADK_VISION,
        help="Send observe() screenshot to the ADK model.",
    )
    parser.add_argument("--no-adk-vision", dest="adk_vision", action="store_false", help="Do not send screenshots to the ADK model.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    return parser.parse_args(argv)


def main() -> None:
    load_project_env()
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    client = InProcessPokemonMcpClient()
    client.start_session(window=args.window, load_fixed=not args.no_load_fixed, control_ui=args.control_ui)
    if args.realtime_ticks:
        client.set_realtime_ticks(enabled=True, fps=args.realtime_fps)
    try:
        action_planner = None
        if args.adk_model:
            action_planner = GoogleAdkPlanner.from_env(
                model=args.adk_model,
                include_screenshot=args.adk_vision,
            )

        idle_pump = client.pump_realtime if args.control_ui or args.realtime_ticks else None
        loop = PokemonAdkLoop(
            client,
            action_planner=action_planner,
            idle_pump=idle_pump,
            idle_pump_interval=1.0 / max(1.0, min(float(args.ui_refresh_hz), 120.0)),
        )
        result = loop.run(
            objective=args.objective,
            max_steps=args.steps,
            checkpoint_every=args.checkpoint_every,
        )
        print(json.dumps(summarize_result(result), indent=2, ensure_ascii=False))
    finally:
        client.stop_session(save_final=args.save_final)


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    observation = result.get("observation", {})
    return {
        "objective": result.get("objective"),
        "done": result.get("done"),
        "step_count": result.get("step_count"),
        "mode": result.get("mode"),
        "stuck_score": result.get("stuck_score"),
        "checkpoint_path": result.get("checkpoint_path"),
        "state": observation.get("state"),
        "last_action": result.get("planned_action"),
        "plan_error": result.get("plan_error"),
        "last_result": {
            key: value
            for key, value in result.get("action_result", {}).items()
            if key not in {"before_observation", "after_observation"}
        },
    }


if __name__ == "__main__":
    main()

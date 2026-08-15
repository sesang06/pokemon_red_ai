from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from pokemon_agent.adk_agent.agents.interpreter.agent import GoogleAdkResultInterpreter
from pokemon_agent.adk_agent.agents.planner.agent import DEFAULT_ADK_MODEL, GoogleAdkPlanner
from pokemon_agent.adk_agent.agents.planner.schema import DEFAULT_MAX_STEPS, DEFAULT_OBJECTIVE
from pokemon_agent.adk_agent.client import InProcessPokemonMcpClient
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.coordinator.workflow_agent import (
    AUTOPLAY_SESSION_ID,
    run_traced_pokemon_loop,
)
from pokemon_agent.adk_agent.runtime.logging import DateGroupedActionLogger
from pokemon_agent.adk_agent.runtime.session import DEFAULT_ADK_SESSION_DB_PATH
from pokemon_agent.adk_agent.runtime.state import DEFAULT_RUNTIME_STATE_PATH, FileAgentRuntimeState
from pokemon_agent.adk_agent.runtime.trace import AgentTracePrinter
from pokemon_agent.dashboard.server import (
    DashboardRuntimeStateStore,
    DashboardService,
    compose_trace_sinks,
)
from pokemon_agent.memory.file_memory import DEFAULT_LONG_TERM_MEMORY_PATH, FileLongTermMemory

DEFAULT_WINDOW = "SDL2"
DEFAULT_CONTROL_UI = True
DEFAULT_REALTIME_TICKS = True
DEFAULT_ADK_VISION = True
DEFAULT_AGENT_TRACE = True
DEFAULT_DASHBOARD = True
DEFAULT_ADK_THINKING_BUDGET = -1
DEFAULT_ACTION_LOG_DIR = Path("logs") / "actions"


def load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return

    project_root = Path(__file__).resolve().parents[3]
    load_dotenv(project_root / ".env")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MCP-backed Google ADK Pokemon Red agent loop.")
    parser.add_argument("--steps", type=int, default=DEFAULT_MAX_STEPS, help="Maximum number of agent action cycles.")
    parser.add_argument(
        "--window",
        default=DEFAULT_WINDOW,
        help="PyBoy backend. With control UI, SDL2 is merged into Qt while audio stays enabled; null is silent/headless.",
    )
    parser.add_argument("--objective", default=DEFAULT_OBJECTIVE, help="Agent objective label.")
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
    parser.add_argument("--ui-refresh-hz", type=float, default=30.0, help="How often to refresh the control panel from cached snapshots.")
    parser.add_argument(
        "--adk-model",
        default=os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
        help=f"Enable Google ADK LLM planning with this model, for example {DEFAULT_ADK_MODEL}.",
    )
    parser.add_argument(
        "--adk-vision",
        dest="adk_vision",
        action="store_true",
        default=DEFAULT_ADK_VISION,
        help="Send observe() screenshot to the ADK model.",
    )
    parser.add_argument("--no-adk-vision", dest="adk_vision", action="store_false", help="Do not send screenshots to the ADK model.")
    parser.add_argument(
        "--adk-thinking-budget",
        type=int,
        default=DEFAULT_ADK_THINKING_BUDGET,
        help="Gemini thinking budget for planner/interpreter. -1 is automatic.",
    )
    parser.add_argument(
        "--no-adk-thinking",
        dest="adk_thinking_budget",
        action="store_const",
        const=None,
        help="Do not send a thinking_config to the ADK model.",
    )
    parser.add_argument(
        "--memory-path",
        type=Path,
        default=DEFAULT_LONG_TERM_MEMORY_PATH,
        help="JSON file used for long-term key/value memory.",
    )
    parser.add_argument(
        "--adk-session-db",
        type=Path,
        default=DEFAULT_ADK_SESSION_DB_PATH,
        help="Shared SQLite session DB used by the CLI and ADK Dev UI.",
    )
    parser.add_argument(
        "--runtime-state-path",
        type=Path,
        default=DEFAULT_RUNTIME_STATE_PATH,
        help="Shared JSON state read by ADK Dev UI history tools.",
    )
    parser.add_argument(
        "--agent-trace",
        dest="agent_trace",
        action="store_true",
        default=DEFAULT_AGENT_TRACE,
        help="Print visible agent thought summaries and decisions while the loop runs.",
    )
    parser.add_argument("--no-agent-trace", dest="agent_trace", action="store_false", help="Disable live agent trace printing.")
    parser.add_argument(
        "--dashboard",
        dest="dashboard",
        action="store_true",
        default=DEFAULT_DASHBOARD,
        help="Serve the real-time Pokemon Red debugger UI.",
    )
    parser.add_argument("--no-dashboard", dest="dashboard", action="store_false", help="Disable the web debugger UI.")
    parser.add_argument("--dashboard-host", default="127.0.0.1", help="Dashboard bind host.")
    parser.add_argument("--dashboard-port", type=int, default=8765, help="Dashboard HTTP/WebSocket port.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    parser.add_argument(
        "--action-log-dir",
        type=Path,
        default=DEFAULT_ACTION_LOG_DIR,
        help="Directory for date-grouped JSONL action logs.",
    )
    parser.add_argument(
        "--no-action-log",
        dest="action_log_dir",
        action="store_const",
        const=None,
        help="Disable date-grouped action logging.",
    )
    return parser.parse_args(argv)


def main() -> None:
    load_project_env()
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    client = InProcessPokemonMcpClient()
    client.start_session(window=args.window, load_fixed=not args.no_load_fixed, control_ui=args.control_ui)
    client.set_realtime_ticks(enabled=args.realtime_ticks, fps=args.realtime_fps)
    dashboard: DashboardService | None = None
    try:
        memory_store = FileLongTermMemory(args.memory_path)
        action_planner = GoogleAdkPlanner.from_env(
            model=args.adk_model,
            include_screenshot=args.adk_vision,
            thinking_budget=args.adk_thinking_budget,
            session_db_path=args.adk_session_db,
            memory_store=memory_store,
        )
        result_interpreter = GoogleAdkResultInterpreter.from_env(
            model=args.adk_model,
            thinking_budget=args.adk_thinking_budget,
            session_db_path=args.adk_session_db,
            memory_store=memory_store,
        )

        idle_pump = client.pump_realtime if args.control_ui or args.realtime_ticks else None
        file_runtime_state_store = FileAgentRuntimeState(
            args.runtime_state_path,
            metadata={
                "session_db": str(args.adk_session_db.resolve()),
                "autoplay_session_id": AUTOPLAY_SESSION_ID,
                "planner_session_id": "pokemon-red-planner",
                "result_interpreter_session_id": "pokemon-red-result-interpreter",
            },
        )
        trace_printer = AgentTracePrinter(enabled=args.agent_trace)
        trace_sink = trace_printer
        runtime_state_store: Any = file_runtime_state_store
        if args.dashboard:
            try:
                from pokemon_agent import mcp_server

                dashboard = DashboardService(host=args.dashboard_host, port=args.dashboard_port)
                dashboard.attach_session(mcp_server.get_session())
                dashboard.hub.publish_memory_snapshot(memory_store.items())
                dashboard.start()
                memory_activity_sink = lambda event: dashboard.hub.publish_memory_activity(
                    memory_store.items(),
                    event,
                )
                action_planner.memory_activity_callback = memory_activity_sink
                result_interpreter.memory_activity_callback = memory_activity_sink
                trace_sink = compose_trace_sinks(trace_printer, dashboard.hub.publish_trace)
                runtime_state_store = DashboardRuntimeStateStore(
                    file_runtime_state_store,
                    dashboard.hub,
                    memory_store=memory_store,
                )
                logging.info("dashboard=%s", dashboard.url)
            except Exception:
                logging.exception("Dashboard failed to start; gameplay and agent execution will continue")
                if dashboard is not None:
                    dashboard.stop()
                dashboard = None
        loop = PokemonAdkLoop(
            client,
            action_planner=action_planner,
            result_interpreter=result_interpreter,
            memory_store=memory_store,
            trace=trace_sink,
            action_logger=None if args.action_log_dir is None else DateGroupedActionLogger(args.action_log_dir),
            idle_pump=idle_pump,
            idle_pump_interval=1.0 / max(1.0, min(float(args.ui_refresh_hz), 120.0)),
            runtime_state_store=runtime_state_store,
        )
        result = run_traced_pokemon_loop(
            loop,
            objective=args.objective,
            max_steps=args.steps,
            checkpoint_every=args.checkpoint_every,
            session_db_path=str(args.adk_session_db),
            idle_pump=idle_pump,
            idle_pump_interval=1.0 / max(1.0, min(float(args.ui_refresh_hz), 120.0)),
        )
        result["adk_thinking_budget"] = args.adk_thinking_budget
        print(json.dumps(summarize_result(result), indent=2, ensure_ascii=False))
    finally:
        if dashboard is not None:
            dashboard.stop()
        client.stop_session(save_final=args.save_final)


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    observation = result.get("observation", {})
    return {
        "objective": result.get("objective"),
        "done": result.get("done"),
        "termination_reason": result.get("termination_reason"),
        "step_count": result.get("step_count"),
        "mode": result.get("mode"),
        "stuck_score": result.get("stuck_score"),
        "checkpoint_path": result.get("checkpoint_path"),
        "fixed_state_path": result.get("fixed_state_path"),
        "memory_path": result.get("memory_path"),
        "state": observation.get("state"),
        "current_goal": result.get("current_goal"),
        "active_action_plan": result.get("active_action_plan"),
        "action_outcome": result.get("action_outcome"),
        "state_diff": result.get("state_diff"),
        "planner_call_count": result.get("planner_call_count"),
        "llm_planner_call_count": result.get("llm_planner_call_count"),
        "interpreter_call_count": result.get("interpreter_call_count"),
        "plan_decision": result.get("plan_decision"),
        "execution_report": result.get("execution_report"),
        "interpretation": result.get("interpretation"),
        "interpret_error": result.get("interpret_error"),
        "history_summary": result.get("history_summary"),
        "adk_thinking_budget": result.get("adk_thinking_budget"),
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

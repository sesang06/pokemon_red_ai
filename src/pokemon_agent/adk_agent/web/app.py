from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.genai import types

from pokemon_agent.adk_agent.agents.interpreter.agent import GoogleAdkResultInterpreter
from pokemon_agent.adk_agent.agents.planner.agent import (
    DEFAULT_ADK_MODEL,
    DEFAULT_ADK_THINKING_LEVEL,
    GoogleAdkPlanner,
)
from pokemon_agent.adk_agent.agents.shared import MAX_AUTOMATIC_FUNCTION_CALLS
from pokemon_agent.adk_agent.client import StdioPokemonMcpClient
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.coordinator.workflow_agent import PokemonRedTeamAgent
from pokemon_agent.adk_agent.runtime.logging import DateGroupedActionLogger
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_ADK_SESSION_DB_PATH,
    PROJECT_ROOT,
    build_events_compaction_config,
)
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState
from pokemon_agent.adk_agent.web.prompt import WEB_AGENT_PROMPT
from pokemon_agent.adk_agent.web.tools import (
    agent_runtime_status,
    recent_agent_actions,
)
from pokemon_agent.dashboard.server import DashboardRuntimeStateStore
from pokemon_agent.memory.file_memory import FileLongTermMemory

LOGGER = logging.getLogger(__name__)


class _McpDashboardHub:
    """Best-effort bridge from the ADK Web process to the MCP worker dashboard."""

    def __init__(self, client: StdioPokemonMcpClient) -> None:
        self.client = client

    def publish_trace(self, trace: dict[str, Any]) -> None:
        self._publish(self.client.publish_dashboard_trace, trace)

    def publish_runtime(self, state: dict[str, Any], *, phase: str) -> None:
        self._publish(
            self.client.publish_dashboard_runtime,
            _compact_dashboard_runtime_state(state),
            phase=phase,
        )

    def publish_memory_snapshot(self, items: dict[str, dict[str, Any]]) -> None:
        self._publish(self.client.publish_dashboard_memory, items)

    def publish_memory_activity(
        self,
        items: dict[str, dict[str, Any]],
        activity: dict[str, Any],
    ) -> None:
        self._publish(self.client.publish_dashboard_memory, items, activity)

    @staticmethod
    def _publish(operation: Any, *args: Any, **kwargs: Any) -> None:
        try:
            operation(*args, **kwargs)
        except Exception:
            LOGGER.debug("MCP dashboard bridge publish failed", exc_info=True)


def load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return

    project_root = Path(__file__).resolve().parents[4]
    load_dotenv(project_root / ".env")


def build_root_agent(model: str | None = None) -> Agent:
    load_project_env()
    selected_model = model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL)
    generate_content_config = types.GenerateContentConfig(
        temperature=0.2,
        maxOutputTokens=900,
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.MEDIUM,
            include_thoughts=False,
        ),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=MAX_AUTOMATIC_FUNCTION_CALLS,
        ),
    )
    autoplay_team = build_autoplay_team(selected_model)
    return Agent(
        name="pokemon_red_web_coordinator",
        model=selected_model,
        description="Routes bounded autoplay requests to the traceable Pokemon Red runtime team.",
        instruction=WEB_AGENT_PROMPT,
        generate_content_config=generate_content_config,
        tools=[
            agent_runtime_status,
            recent_agent_actions,
        ],
        sub_agents=[autoplay_team],
    )


def build_autoplay_team(model: str | None = None) -> PokemonRedTeamAgent:
    load_project_env()
    selected_model = model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL)
    memory_store = FileLongTermMemory()
    client = StdioPokemonMcpClient(
        window="SDL2",
        load_fixed=True,
        control_ui=True,
        realtime_fps=60.0,
        ui_refresh_hz=30.0,
        dashboard=True,
    )
    dashboard_hub = _McpDashboardHub(client)
    planner = GoogleAdkPlanner.from_env(
        model=selected_model,
        include_screenshot=True,
        thinking_level=DEFAULT_ADK_THINKING_LEVEL,
        memory_store=memory_store,
    )
    interpreter = GoogleAdkResultInterpreter.from_env(
        model=selected_model,
        thinking_level=DEFAULT_ADK_THINKING_LEVEL,
        memory_store=memory_store,
    )
    memory_activity_sink = lambda event: dashboard_hub.publish_memory_activity(
        memory_store.items(),
        event,
    )
    planner.memory_activity_callback = memory_activity_sink
    interpreter.memory_activity_callback = memory_activity_sink
    file_runtime_state_store = FileAgentRuntimeState(
        metadata={
            "session_db": str(DEFAULT_ADK_SESSION_DB_PATH.resolve()),
            "autoplay_session_id": "current-dev-ui-invocation",
        }
    )
    loop = PokemonAdkLoop(
        client,
        action_planner=planner,
        result_interpreter=interpreter,
        memory_store=memory_store,
        trace=dashboard_hub.publish_trace,
        action_logger=DateGroupedActionLogger(PROJECT_ROOT / "logs" / "actions"),
        runtime_state_store=DashboardRuntimeStateStore(
            file_runtime_state_store,
            dashboard_hub,
            memory_store=memory_store,
        ),
    )

    def start_worker() -> dict[str, Any]:
        started = client.start_session(window="SDL2", load_fixed=True, control_ui=True)
        client.set_realtime_ticks(enabled=True, fps=60.0)
        dashboard_hub.publish_memory_snapshot(memory_store.items())
        return started

    return PokemonRedTeamAgent(
        loop=loop,
        max_steps=None,
        checkpoint_every=10,
        startup=start_worker,
        shutdown=lambda: client.stop_session(save_final=False),
    )


def _compact_dashboard_runtime_state(state: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "goal",
        "active_action_plan",
        "action_outcome",
        "state_diff",
        "step_count",
        "max_steps",
        "planner_call_count",
        "interpreter_call_count",
        "plan_error",
        "interpret_error",
        "done",
        "termination_reason",
    )
    compact = {field: state.get(field) for field in fields}
    action_result = state.get("action_result")
    if isinstance(action_result, dict):
        compact["action_result"] = {
            key: value
            for key, value in action_result.items()
            if key not in {"before_observation", "after_observation"}
        }
    return compact


def build_app(model: str | None = None) -> App:
    return _build_app_for_root(build_root_agent(model))


def _build_app_for_root(agent: Agent) -> App:
    return App(
        name=ADK_WEB_APP_NAME,
        root_agent=agent,
        events_compaction_config=build_events_compaction_config(),
    )


root_agent = build_root_agent()
app = _build_app_for_root(root_agent)

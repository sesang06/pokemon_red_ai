from __future__ import annotations

import os
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.genai import types

from pokemon_agent.adk_agent.agents.interpreter.prompt import RESULT_INTERPRETER_PROMPT
from pokemon_agent.adk_agent.agents.planner.agent import DEFAULT_ADK_MODEL
from pokemon_agent.adk_agent.agents.planner.prompt import PLANNING_AGENT_PROMPT
from pokemon_agent.adk_agent.runtime.session import ADK_WEB_APP_NAME, build_events_compaction_config
from pokemon_agent.adk_agent.web.prompt import WEB_AGENT_PROMPT
from pokemon_agent.adk_agent.web.tools import (
    agent_runtime_status,
    read_long_term_memory,
    observe_game,
    realtime_tick_status,
    recent_agent_actions,
    recent_game_commands,
    recent_session_dialog,
    run_rule_based_step,
    run_team_step,
    save_current_screenshot,
    search_long_term_memory,
    set_realtime_ticks,
    start_game,
    stop_game,
    write_long_term_memory,
)


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
    )
    planning_agent = Agent(
        name="pokemon_red_planning_agent",
        model=selected_model,
        description="Returns one bounded buttons or world-coordinate move plan from observation and memory.",
        instruction=PLANNING_AGENT_PROMPT,
        generate_content_config=generate_content_config,
        tools=[
            start_game,
            observe_game,
            save_current_screenshot,
            read_long_term_memory,
            search_long_term_memory,
            recent_game_commands,
            recent_agent_actions,
            recent_session_dialog,
        ],
    )
    result_interpreter_agent = Agent(
        name="pokemon_red_result_interpreter_agent",
        model=selected_model,
        description="Interprets meaningful action outcomes and writes durable file-backed memory.",
        instruction=RESULT_INTERPRETER_PROMPT,
        generate_content_config=generate_content_config,
        tools=[
            observe_game,
            recent_game_commands,
            read_long_term_memory,
            search_long_term_memory,
            write_long_term_memory,
        ],
    )
    return Agent(
        name="pokemon_red_team",
        model=selected_model,
        description="Coordinates direct LLM action planning with deterministic execution, repetition, and verification.",
        instruction=WEB_AGENT_PROMPT,
        generate_content_config=generate_content_config,
        tools=[
            start_game,
            stop_game,
            set_realtime_ticks,
            realtime_tick_status,
            run_team_step,
            run_rule_based_step,
            recent_game_commands,
            agent_runtime_status,
            recent_agent_actions,
            recent_session_dialog,
        ],
        sub_agents=[
            planning_agent,
            result_interpreter_agent,
        ],
    )


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

from __future__ import annotations

import os
from pathlib import Path

from google.adk.agents import Agent
from google.genai import types

from pokemon_agent.adk_agent.adk_planner import DEFAULT_ADK_MODEL
from pokemon_agent.adk_agent.web_tools import (
    move_to_screen_tile,
    observe_game,
    press_button,
    realtime_tick_status,
    recent_game_commands,
    run_rule_based_step,
    save_current_screenshot,
    set_realtime_ticks,
    start_game,
    step_frames,
    stop_game,
)


WEB_AGENT_PROMPT = """You are a Pokemon Red ADK Web control agent.
Use tools to inspect and control the local PyBoy Pokemon Red session.

Important behavior:
- If the game is not started, call start_game first.
- Use observe_game to inspect RAM-derived state, screen tiles, collision, world map, and screenshot metadata.
- Use save_current_screenshot when the user wants to visually confirm the current screenshot in a saved PNG file.
- Use move_to_screen_tile for overworld movement instead of inventing raw button sequences.
- Use press_button for dialog, battle, menu, and simple confirmations.
- Use run_rule_based_step for one safe non-LLM autonomous step.
- Use recent_game_commands when the user asks what commands were sent.
- Keep actions small and reversible.
- Explain what you did briefly after tool calls.

The ADK Web event history should show every tool call and result. Do not output API keys or secrets.
"""


def load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return

    project_root = Path(__file__).resolve().parents[3]
    load_dotenv(project_root / ".env")


def build_root_agent(model: str | None = None) -> Agent:
    load_project_env()
    return Agent(
        name="pokemon_red_planner",
        model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
        description="Controls and observes a local Pokemon Red PyBoy session through ADK Web tools.",
        instruction=WEB_AGENT_PROMPT,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            maxOutputTokens=300,
        ),
        tools=[
            start_game,
            stop_game,
            observe_game,
            save_current_screenshot,
            press_button,
            move_to_screen_tile,
            step_frames,
            set_realtime_ticks,
            realtime_tick_status,
            run_rule_based_step,
            recent_game_commands,
        ],
    )


root_agent = build_root_agent()

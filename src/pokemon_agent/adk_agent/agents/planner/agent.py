from __future__ import annotations

import base64
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pokemon_agent.adk_agent.agents.planner.prompt import PLANNING_AGENT_PROMPT
from pokemon_agent.adk_agent.agents.memory_tools import (
    build_save_memory_tool,
    build_search_memory_tool,
)
from pokemon_agent.adk_agent.agents.planner.schema import (
    ActionPlanner,
    PokemonAgentState,
    compact_state_for_prompt,
    normalize_action_plan,
)
from pokemon_agent.adk_agent.agents.shared import (
    MAX_AUTOMATIC_FUNCTION_CALLS,
    TraceSink,
    emit_trace,
    parse_json_object,
    public_output_fields,
    run_with_idle_pump,
)
from pokemon_agent.memory.file_memory import FileLongTermMemory

DEFAULT_ADK_MODEL = "gemini-3.5-flash"
DEFAULT_ADK_THINKING_LEVEL = "MEDIUM"
LOGGER = logging.getLogger(__name__)
SYSTEM_PROMPT = PLANNING_AGENT_PROMPT


@dataclass
class PlanningAgent:
    action_planner: ActionPlanner | None = None
    idle_pump: Callable[[], Any] | None = None
    idle_pump_interval: float = 1 / 30
    trace: TraceSink | None = None
    name: str = "pokemon_red_planning_agent"

    def plan(self, state: PokemonAgentState) -> PokemonAgentState:
        enriched_state = dict(state)

        raw_decision: dict[str, Any] | None = None
        plan_error: str | None = None
        if self.action_planner is not None:
            try:
                planning_wait_trace = None
                if not bool(getattr(self.action_planner, "stream_output", False)):
                    planning_wait_trace = lambda elapsed: emit_trace(
                        self.trace,
                        {
                            "agent": self.name,
                            "phase": "planning_wait",
                            "step": state.get("step_count", 0),
                            "elapsed_seconds": round(elapsed, 1),
                        },
                    )
                raw_decision = run_with_idle_pump(
                    lambda: self.action_planner.plan(dict(enriched_state)),
                    idle_pump=self.idle_pump,
                    idle_pump_interval=self.idle_pump_interval,
                    on_wait=planning_wait_trace,
                )
            except Exception as exc:
                plan_error = f"{type(exc).__name__}: {exc}"
                LOGGER.warning("LLM action planning failed: %s", plan_error)
                emit_trace(
                    self.trace,
                    {
                        "agent": self.name,
                        "phase": "planning_error",
                        "step": state.get("step_count", 0),
                        "error": plan_error,
                    },
                )

        active_plan = normalize_action_plan(raw_decision)
        if active_plan is None:
            if plan_error is None:
                plan_error = (
                    "planner_unavailable"
                    if self.action_planner is None
                    else str(
                        getattr(self.action_planner, "last_plan_error", None)
                        or ("invalid_action_plan" if raw_decision is not None else "planner_returned_no_action_plan")
                    )
                )
            LOGGER.warning("No valid LLM action plan; stopping without game input: %s", plan_error)
            emit_trace(
                self.trace,
                {
                    "agent": self.name,
                    "phase": "planning_error",
                    "step": state.get("step_count", 0),
                    "error": plan_error,
                },
            )
            return {
                "active_action_plan": {},
                "plan_error": plan_error,
                "termination_reason": "planning_failed",
                "done": True,
                "planner_call_count": int(state.get("planner_call_count", 0)) + 1,
                "llm_planner_call_count": int(state.get("llm_planner_call_count", 0))
                + int(self.action_planner is not None),
            }

        memory_keys = list(getattr(self.action_planner, "last_memory_search_keys", []))
        active_plan["source"] = "adk"
        active_plan["action"]["source"] = active_plan["source"]
        plan_decision = _normalize_action_plan_decision(
            raw=raw_decision,
            active_plan=active_plan,
            state=enriched_state,
            memory_keys=memory_keys,
        )
        emit_trace(
            self.trace,
            {
                "agent": self.name,
                "phase": "planning_done",
                "step": state.get("step_count", 0),
                "memory_keys_read": plan_decision.get("memory_keys_read"),
                "action_plan": active_plan,
                "screen_description": plan_decision["screen_description"],
                "current_location": plan_decision["current_location"],
                "thought_summary": plan_decision["thought_summary"],
                "error": plan_error,
            },
        )

        return {
            "active_action_plan": active_plan,
            "plan_decision": plan_decision,
            "plan_error": plan_error,
            "planner_call_count": int(state.get("planner_call_count", 0)) + 1,
            "llm_planner_call_count": int(state.get("llm_planner_call_count", 0))
            + int(self.action_planner is not None),
        }


@dataclass
class GoogleAdkPlanner:
    """Builds the native Planner child used by PokemonRedTeamAgent.

    This class deliberately does not own an ADK Runner or session. The outer team
    owns one App/Runner/session so CLI and Dev UI have the same trace and context.
    """

    model: str = DEFAULT_ADK_MODEL
    include_screenshot: bool = False
    temperature: float = 0.2
    max_output_tokens: int = 4096
    thinking_level: str = DEFAULT_ADK_THINKING_LEVEL
    memory_store: FileLongTermMemory = field(default_factory=FileLongTermMemory)
    memory_activity_callback: Callable[[dict[str, Any]], None] | None = field(init=False, default=None, repr=False)
    memory_tool_activity: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        try:
            from google.adk.agents import Agent
            from google.genai import types
        except ModuleNotFoundError as exc:
            raise RuntimeError('Install Google ADK first: python -m pip install -e ".[dev]"') from exc

        config_kwargs: dict[str, Any] = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_output_tokens,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=MAX_AUTOMATIC_FUNCTION_CALLS,
            ),
        }
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=types.ThinkingLevel(self.thinking_level.upper()),
            include_thoughts=False,
        )
        generate_config = types.GenerateContentConfig(**config_kwargs)
        self.agent = Agent(
            name="pokemon_red_planning_agent",
            model=self.model,
            description="Creates one bounded Pokemon Red direct action plan for one-shot execution as JSON.",
            instruction=SYSTEM_PROMPT,
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            generate_content_config=generate_config,
            tools=[
                build_search_memory_tool(
                    self.memory_store,
                    activity=self.memory_tool_activity,
                    on_activity=self._publish_memory_activity,
                ),
                build_save_memory_tool(
                    self.memory_store,
                    source="planning_agent",
                    activity=self.memory_tool_activity,
                    on_activity=self._publish_memory_activity,
                ),
            ],
        )

    def _publish_memory_activity(self, event: dict[str, Any]) -> None:
        if self.memory_activity_callback is not None:
            self.memory_activity_callback(dict(event))

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        include_screenshot: bool = False,
        thinking_level: str = DEFAULT_ADK_THINKING_LEVEL,
        memory_store: FileLongTermMemory | None = None,
    ) -> "GoogleAdkPlanner":
        return cls(
            model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
            include_screenshot=include_screenshot,
            thinking_level=thinking_level,
            memory_store=memory_store or FileLongTermMemory(),
        )

    @property
    def last_memory_search_keys(self) -> list[str]:
        return list(
            dict.fromkeys(
                str(key)
                for entry in self.memory_tool_activity
                if entry.get("tool") == "search_memory"
                for key in entry.get("keys", [])
            )
        )

    def _content_for_state(self, state: dict[str, Any]) -> Any:
        from google.genai import types

        text = json.dumps(
            compact_state_for_prompt(state),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        LOGGER.debug("ADK planner prompt payload chars=%d", len(text))
        parts: list[Any] = [types.Part.from_text(text=text)]

        screenshot = state.get("observation", {}).get("screenshot", {})
        screenshot_base64 = screenshot.get("base64")
        if self.include_screenshot and screenshot_base64:
            parts.append(
                types.Part.from_bytes(
                    data=base64.b64decode(screenshot_base64),
                    mime_type="image/png",
                    media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM,
                )
            )

        overlay = state.get("observation", {}).get("screenshot_overlay", {})
        overlay_base64 = overlay.get("base64")
        if self.include_screenshot and overlay_base64:
            parts.append(
                types.Part.from_text(
                    text=(
                        "Collision overlay image: green=walkable, red=blocked, "
                        "yellow=player, labels are map coordinates. "
                        "Use overlay labels for a semantic world-coordinate target [x,y]. "
                        "The Python executor owns collision checks and pathfinding."
                    )
                )
            )
            parts.append(
                types.Part.from_bytes(
                    data=base64.b64decode(overlay_base64),
                    mime_type="image/png",
                    media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM,
                )
            )

        return types.Content(role="user", parts=parts)


def parse_planner_response(content: str) -> dict[str, Any] | None:
    """Parse the strict response contract and recover a labeled action response."""
    parsed = parse_json_object(content)
    if not isinstance(parsed, dict):
        return None

    if isinstance(parsed.get("action"), dict):
        return parsed
    if parsed.get("type") not in {"buttons", "move"}:
        return None

    recovered: dict[str, Any] = {"action": parsed}
    labels = {
        "screen_description": ("화면 설명", "screen_description"),
        "current_location": ("현재 위치", "current_location"),
        "thought_summary": ("생각 요약", "thought_summary"),
    }
    for field, aliases in labels.items():
        alternatives = "|".join(re.escape(alias) for alias in aliases)
        match = re.search(
            rf"(?im)^\s*(?:{alternatives})\s*:\s*(.+?)\s*$",
            content,
        )
        if match:
            recovered[field] = match.group(1).strip()
    return recovered


def _normalize_action_plan_decision(
    raw: dict[str, Any] | None,
    *,
    active_plan: dict[str, Any],
    state: dict[str, Any],
    memory_keys: list[str],
) -> dict[str, Any]:
    action = active_plan.get("action", {})
    public_fields = public_output_fields(
        raw,
        state,
        default_thought=(
            f"Execute {action.get('reason') or action.get('type') or 'the next action'} and observe the updated state."
        ),
    )
    return {
        "agent": "pokemon_red_planning_agent",
        "phase": "planning",
        "goal": dict(state.get("goal") or {}),
        "action_plan": active_plan,
        "memory_keys_read": memory_keys,
        "reason": action.get("reason"),
        **public_fields,
    }

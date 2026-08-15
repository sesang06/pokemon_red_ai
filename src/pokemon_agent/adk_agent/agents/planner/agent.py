from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pokemon_agent.adk_agent.agents.planner.prompt import PLANNING_AGENT_PROMPT
from pokemon_agent.adk_agent.agents.memory_tools import build_search_memory_tool
from pokemon_agent.adk_agent.agents.planner.schema import (
    ActionPlanner,
    DEFAULT_OBJECTIVE,
    PokemonAgentState,
    compact_state_for_prompt,
    normalize_action_plan,
)
from pokemon_agent.adk_agent.agents.shared import (
    ConsoleTokenStream,
    MAX_AUTOMATIC_FUNCTION_CALLS,
    TraceSink,
    emit_trace,
    event_finish_reason,
    event_text,
    event_thinking_summary,
    invalid_response_error,
    parse_json_object,
    public_output_fields,
    run_with_idle_pump,
)
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_ADK_USER_ID,
    DEFAULT_COMPACTION_INTERVAL,
    DEFAULT_COMPACTION_OVERLAP_SIZE,
    ContextFilteringSqliteSessionService,
    build_events_compaction_config,
)
from pokemon_agent.memory.file_memory import FileLongTermMemory

DEFAULT_ADK_MODEL = "gemini-3.5-flash"
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
            supports_thinking_callback = hasattr(self.action_planner, "thinking_summary_callback")
            if supports_thinking_callback:
                self.action_planner.thinking_summary_callback = lambda summary: emit_trace(
                    self.trace,
                    {
                        "agent": self.name,
                        "phase": "planning_thinking",
                        "step": state.get("step_count", 0),
                        "thinking_summary": summary,
                    },
                )
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
            finally:
                if supports_thinking_callback:
                    self.action_planner.thinking_summary_callback = None

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
                "planned_action": {},
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
                "thinking_summary": getattr(self.action_planner, "last_thinking_summary", None),
                "error": plan_error,
            },
        )

        return {
            "active_action_plan": active_plan,
            "plan_decision": plan_decision,
            "planned_action": dict(active_plan["action"]),
            "plan_error": plan_error,
            "planner_call_count": int(state.get("planner_call_count", 0)) + 1,
            "llm_planner_call_count": int(state.get("llm_planner_call_count", 0))
            + int(self.action_planner is not None),
        }


@dataclass
class GoogleAdkPlanner:
    model: str = DEFAULT_ADK_MODEL
    include_screenshot: bool = False
    app_name: str = ADK_WEB_APP_NAME
    user_id: str = DEFAULT_ADK_USER_ID
    session_id: str = "pokemon-red-planner"
    temperature: float = 0.2
    max_output_tokens: int = 4096
    thinking_budget: int | None = -1
    stream_output: bool = True
    compaction_interval: int = DEFAULT_COMPACTION_INTERVAL
    compaction_overlap_size: int = DEFAULT_COMPACTION_OVERLAP_SIZE
    session_db_path: str | os.PathLike[str] | None = None
    memory_store: FileLongTermMemory = field(default_factory=FileLongTermMemory)
    last_plan_error: str | None = field(init=False, default=None)
    last_finish_reason: str | None = field(init=False, default=None)
    last_thinking_summary: str | None = field(init=False, default=None)
    thinking_summary_callback: Callable[[str], None] | None = field(init=False, default=None, repr=False)
    memory_activity_callback: Callable[[dict[str, Any]], None] | None = field(init=False, default=None, repr=False)
    memory_tool_activity: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        try:
            from google.adk.agents import Agent
            from google.adk.agents.run_config import RunConfig, StreamingMode
            from google.adk.apps.app import App
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
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
        if self.thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self.thinking_budget,
                include_thoughts=True,
            )
        generate_config = types.GenerateContentConfig(**config_kwargs)
        self.session_service = (
            ContextFilteringSqliteSessionService(
                self.session_db_path,
                prior_turn_limit=None,
            )
            if self.session_db_path is not None
            else InMemorySessionService()
        )
        self.agent = Agent(
            name="pokemon_red_planner",
            model=self.model,
            description="Creates one bounded Pokemon Red direct action plan for one-shot execution as JSON.",
            instruction=SYSTEM_PROMPT,
            generate_content_config=generate_config,
            tools=[
                build_search_memory_tool(
                    self.memory_store,
                    activity=self.memory_tool_activity,
                    on_activity=self._publish_memory_activity,
                )
            ],
        )
        self.app = App(
            name=self.app_name,
            root_agent=self.agent,
            events_compaction_config=build_events_compaction_config(
                interval=self.compaction_interval,
                overlap_size=self.compaction_overlap_size,
            ),
        )
        self.runner = Runner(
            app=self.app,
            session_service=self.session_service,
        )
        self.run_config = RunConfig(
            streaming_mode=StreamingMode.SSE if self.stream_output else StreamingMode.NONE,
        )
        self._session_created = False

    def _publish_memory_activity(self, event: dict[str, Any]) -> None:
        if self.memory_activity_callback is not None:
            self.memory_activity_callback(dict(event))

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        include_screenshot: bool = False,
        thinking_budget: int | None = -1,
        stream_output: bool = True,
        session_db_path: str | os.PathLike[str] | None = None,
        memory_store: FileLongTermMemory | None = None,
    ) -> "GoogleAdkPlanner":
        return cls(
            model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
            include_screenshot=include_screenshot,
            thinking_budget=thinking_budget,
            stream_output=stream_output,
            session_db_path=session_db_path,
            memory_store=memory_store or FileLongTermMemory(),
        )

    def plan(self, state: dict[str, Any]) -> dict[str, Any] | None:
        return asyncio.run(self.plan_async(state))

    async def plan_async(self, state: dict[str, Any]) -> dict[str, Any] | None:
        self.last_plan_error = None
        self.last_finish_reason = None
        self.last_thinking_summary = None
        if not hasattr(self, "memory_tool_activity"):
            self.memory_tool_activity = []
        else:
            self.memory_tool_activity.clear()
        await self._ensure_session()
        _strip_prior_media_from_session_service(
            self.session_service,
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        content = self._content_for_state(state)
        final_text = ""
        streamed_text = ""
        streamed_thinking = ""
        final_thinking = ""
        console_stream = ConsoleTokenStream("pokemon_red_planner", enabled=self.stream_output)
        async for event in self.runner.run_async(
            user_id=self.user_id,
            session_id=self.session_id,
            new_message=content,
            run_config=self.run_config,
        ):
            text = event_text(event)
            thinking = event_thinking_summary(event)
            if thinking:
                if getattr(event, "partial", False):
                    streamed_thinking += thinking
                else:
                    final_thinking = (
                        thinking
                        if not streamed_thinking or thinking.startswith(streamed_thinking)
                        else streamed_thinking + thinking
                    )
                current_thinking = (final_thinking or streamed_thinking).strip()
                if current_thinking:
                    self.last_thinking_summary = current_thinking
                    callback = getattr(self, "thinking_summary_callback", None)
                    if callback is not None:
                        callback(current_thinking)
            if getattr(event, "partial", False) and text:
                streamed_text += text
                console_stream.write(text)
            elif text:
                final_text = text
            finish_reason = event_finish_reason(event)
            if finish_reason:
                self.last_finish_reason = finish_reason
            if event.is_final_response() and text:
                final_text = text
        if not final_text:
            final_text = streamed_text
        console_stream.finish(final_text)
        action = parse_planner_response(final_text)
        if isinstance(action, dict):
            action.setdefault("source", "adk")
            return action
        self.last_plan_error = invalid_response_error(
            final_text,
            finish_reason=self.last_finish_reason,
        )
        LOGGER.warning(
            "ADK planner response rejected: %s; preview=%r",
            self.last_plan_error,
            final_text[:500],
        )
        return None

    @property
    def last_memory_search_keys(self) -> list[str]:
        return list(
            dict.fromkeys(
                str(entry["key"])
                for entry in self.memory_tool_activity
                if entry.get("tool") == "search_memory" and entry.get("key")
            )
        )

    async def _ensure_session(self) -> None:
        if self._session_created:
            return
        from google.adk.sessions.base_session_service import GetSessionConfig

        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.session_id,
            config=GetSessionConfig(num_recent_events=0),
        )
        if session is None:
            await self.session_service.create_session(
                app_name=self.app_name,
                user_id=self.user_id,
                session_id=self.session_id,
            )
        self._session_created = True

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
                )
            )

        return types.Content(role="user", parts=parts)


def _strip_prior_media_from_session_service(
    session_service: Any,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
) -> int:
    sessions = getattr(session_service, "sessions", None)
    if not isinstance(sessions, dict):
        return 0

    session = sessions.get(app_name, {}).get(user_id, {}).get(session_id)
    events = getattr(session, "events", None)
    if not isinstance(events, list):
        return 0

    removed = 0
    for event in events:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None)
        if not parts:
            continue

        kept_parts = []
        event_removed = 0
        for part in parts:
            if _part_has_media_payload(part):
                removed += 1
                event_removed += 1
                continue
            kept_parts.append(part)

        if event_removed:
            kept_parts.append(_omitted_prior_media_part(event_removed))
            content.parts = kept_parts

    return removed


def _part_has_media_payload(part: Any) -> bool:
    return bool(getattr(part, "inline_data", None) or getattr(part, "file_data", None))


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


def _omitted_prior_media_part(count: int) -> Any:
    from google.genai import types

    noun = "image" if count == 1 else "images"
    return types.Part.from_text(
        text=(
            f"[{count} prior media {noun} omitted from this request. "
            "Use only the latest screenshot and overlay attached to the current user message.]"
        )
    )


def _normalize_action_plan_decision(
    raw: dict[str, Any] | None,
    *,
    active_plan: dict[str, Any],
    state: dict[str, Any],
    memory_keys: list[str],
) -> dict[str, Any]:
    action = active_plan.get("action", {})
    current_goal = str(state.get("current_goal", {}).get("id") or state.get("objective") or DEFAULT_OBJECTIVE)
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
        "objective": state.get("objective"),
        "current_goal": current_goal,
        "action_plan": active_plan,
        "memory_keys_read": memory_keys,
        "reason": action.get("reason"),
        **public_fields,
    }

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pokemon_agent.adk_agent.agents.planner.prompt import PLANNING_AGENT_PROMPT
from pokemon_agent.adk_agent.agents.planner.schema import (
    ActionPlanner,
    PokemonAgentState,
    compact_state_for_prompt,
    normalize_action_plan,
    rule_based_plan,
)
from pokemon_agent.adk_agent.agents.shared import (
    ConsoleTokenStream,
    TraceSink,
    emit_trace,
    event_finish_reason,
    event_text,
    invalid_response_error,
    parse_json_object,
    run_with_idle_pump,
)
from pokemon_agent.adk_agent.runtime.history import RAW_HISTORY_TURNS
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_ADK_USER_ID,
    DEFAULT_COMPACTION_INTERVAL,
    DEFAULT_COMPACTION_OVERLAP_SIZE,
    ContextFilteringSqliteSessionService,
    build_events_compaction_config,
)
from pokemon_agent.memory.file_memory import FileLongTermMemory

DEFAULT_ADK_MODEL = "gemini-2.5-flash"
LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = PLANNING_AGENT_PROMPT


@dataclass
class PlanningAgent:
    memory_store: FileLongTermMemory
    action_planner: ActionPlanner | None = None
    idle_pump: Callable[[], Any] | None = None
    idle_pump_interval: float = 1 / 30
    trace: TraceSink | None = None
    name: str = "pokemon_red_planning_agent"
    history_limit: int = RAW_HISTORY_TURNS

    def plan(self, state: PokemonAgentState) -> PokemonAgentState:
        memory_context = self.memory_store.relevant_for_state(dict(state))
        enriched_state = dict(state)
        enriched_state["long_term_memory"] = memory_context

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
                            "thought_summary": (
                                "LLM planner is still preparing a visible decision summary "
                                f"({elapsed:.1f}s)."
                            ),
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
                LOGGER.warning("LLM action planning failed; using deterministic fallback: %s", plan_error)
                emit_trace(
                    self.trace,
                    {
                        "agent": self.name,
                        "phase": "planning_error",
                        "step": state.get("step_count", 0),
                        "thought_summary": "LLM planning failed; falling back to the rule-based planner.",
                        "error": plan_error,
                    },
                )

        active_plan = normalize_action_plan(raw_decision)
        if active_plan is None:
            fallback_action = rule_based_plan(enriched_state).get("planned_action")
            active_plan = normalize_action_plan({"action": fallback_action})
            if active_plan is None:
                active_plan = normalize_action_plan(
                    {"action": {"type": "buttons", "buttons": ["wait"], "reason": "planner_fallback_wait"}}
                )
            assert active_plan is not None
            if self.action_planner is not None and plan_error is None:
                if raw_decision is None:
                    plan_error = str(
                        getattr(self.action_planner, "last_plan_error", None)
                        or "planner_returned_no_action_plan"
                    )
                else:
                    plan_error = "invalid_action_plan"
                LOGGER.warning("LLM action plan rejected; using deterministic fallback: %s", plan_error)

        planner_used = self.action_planner is not None and raw_decision is not None and plan_error is None
        active_plan["source"] = "adk" if planner_used else "rule"
        active_plan["action"]["source"] = active_plan["source"]
        plan_decision = _normalize_action_plan_decision(
            raw_decision if isinstance(raw_decision, dict) else {},
            active_plan=active_plan,
            state=enriched_state,
            memory_keys=list(memory_context.get("keys", [])),
            planner_used=planner_used,
        )
        emit_trace(
            self.trace,
            {
                "agent": self.name,
                "phase": "planning_done",
                "step": state.get("step_count", 0),
                "thought_summary": plan_decision.get("thought_summary"),
                "session_dialog": plan_decision.get("session_dialog"),
                "decision_trace": plan_decision.get("decision_trace"),
                "memory_keys_read": plan_decision.get("memory_keys_read"),
                "action_plan": active_plan,
                "expected_result": active_plan.get("repeat_until") or "single bounded action",
                "error": plan_error,
            },
        )
        session_dialog = list(state.get("session_dialog", []))
        session_dialog.append(_session_dialog_entry(plan_decision, step=state.get("step_count", 0)))
        session_dialog = session_dialog[-self.history_limit :]

        return {
            "long_term_memory": memory_context,
            "active_action_plan": active_plan,
            "plan_decision": plan_decision,
            "planned_action": dict(active_plan["action"]),
            "plan_error": plan_error,
            "session_dialog": session_dialog,
            "planner_call_count": int(state.get("planner_call_count", 0)) + 1,
            "llm_planner_call_count": int(state.get("llm_planner_call_count", 0))
            + int(self.action_planner is not None),
            "replan_required": False,
        }


@dataclass
class GoogleAdkPlanner:
    model: str = DEFAULT_ADK_MODEL
    include_screenshot: bool = False
    app_name: str = ADK_WEB_APP_NAME
    user_id: str = DEFAULT_ADK_USER_ID
    session_id: str = "pokemon-red-safe-loop"
    temperature: float = 0.2
    max_output_tokens: int = 4096
    thinking_budget: int | None = -1
    stream_output: bool = True
    compaction_interval: int = DEFAULT_COMPACTION_INTERVAL
    compaction_overlap_size: int = DEFAULT_COMPACTION_OVERLAP_SIZE
    session_db_path: str | os.PathLike[str] | None = None
    last_plan_error: str | None = field(init=False, default=None)
    last_finish_reason: str | None = field(init=False, default=None)

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
            "responseMimeType": "application/json",
        }
        if self.thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinkingBudget=self.thinking_budget)
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
            description="Creates one bounded Pokemon Red direct action plan with an optional repeat condition as JSON.",
            instruction=SYSTEM_PROMPT,
            generate_content_config=generate_config,
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

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        include_screenshot: bool = False,
        thinking_budget: int | None = -1,
        stream_output: bool = True,
        session_db_path: str | os.PathLike[str] | None = None,
    ) -> "GoogleAdkPlanner":
        return cls(
            model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
            include_screenshot=include_screenshot,
            thinking_budget=thinking_budget,
            stream_output=stream_output,
            session_db_path=session_db_path,
        )

    def plan(self, state: dict[str, Any]) -> dict[str, Any] | None:
        return asyncio.run(self.plan_async(state))

    async def plan_async(self, state: dict[str, Any]) -> dict[str, Any] | None:
        self.last_plan_error = None
        self.last_finish_reason = None
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
        console_stream = ConsoleTokenStream("pokemon_red_planner", enabled=self.stream_output)
        async for event in self.runner.run_async(
            user_id=self.user_id,
            session_id=self.session_id,
            new_message=content,
            run_config=self.run_config,
        ):
            text = event_text(event)
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
        action = parse_json_object(final_text)
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


def _omitted_prior_media_part(count: int) -> Any:
    from google.genai import types

    noun = "image" if count == 1 else "images"
    return types.Part.from_text(
        text=(
            f"[{count} prior media {noun} omitted from this request. "
            "Use only the latest screenshot and overlay attached to the current user message.]"
        )
    )


def _session_dialog_entry(plan_decision: dict[str, Any], *, step: int) -> dict[str, Any]:
    return {
        "agent": plan_decision.get("agent", "pokemon_red_planning_agent"),
        "phase": "planning_dialog",
        "step": step,
        "content": plan_decision.get("session_dialog"),
        "screen_description": plan_decision.get("screen_description"),
        "current_location": plan_decision.get("current_location"),
        "current_goal": plan_decision.get("current_goal"),
        "future_objective": plan_decision.get("future_objective"),
        "decision_rationale": plan_decision.get("decision_rationale"),
        "action_plan": plan_decision.get("action_plan"),
    }


def _normalize_action_plan_decision(
    raw: dict[str, Any],
    *,
    active_plan: dict[str, Any],
    state: dict[str, Any],
    memory_keys: list[str],
    planner_used: bool,
) -> dict[str, Any]:
    action = active_plan.get("action", {})
    screen_description = _text_or_default(raw.get("screen_description"), _default_screen_description(state))
    current_location = _text_or_default(raw.get("current_location"), _default_current_location(state))
    current_goal = _text_or_default(
        raw.get("current_goal") or raw.get("goal"),
        str(state.get("current_goal", {}).get("id") or state.get("objective") or "safe_loop"),
    )
    future_objective = _text_or_default(
        raw.get("future_objective"),
        (
            f"Execute {action.get('type')} and observe whether "
            f"{active_plan.get('repeat_until') or 'the state changes as expected'}."
        ),
    )
    decision_rationale = _text_or_default(
        raw.get("decision_rationale"),
        (
            f"The {'LLM' if planner_used else 'rule-based'} planner selected a bounded "
            f"{action.get('type')} action. Python may repeat it up to "
            f"{active_plan.get('max_repeats', 1)} time(s) and checks "
            f"{active_plan.get('repeat_until') or 'the result after one action'} from RAM-derived state."
        ),
    )
    session_dialog = _text_or_default(
        raw.get("session_dialog"),
        _default_session_dialog(
            screen_description=screen_description,
            current_location=current_location,
            current_goal=current_goal,
            future_objective=future_objective,
            decision_rationale=decision_rationale,
        ),
    )
    return {
        "agent": "pokemon_red_planning_agent",
        "phase": "planning",
        "objective": state.get("objective"),
        "current_goal": current_goal,
        "action_plan": active_plan,
        "memory_keys_read": memory_keys,
        "screen_description": screen_description,
        "current_location": current_location,
        "future_objective": future_objective,
        "thought_summary": raw.get("thought_summary") or f"Selected a bounded {action.get('type')} action plan.",
        "decision_rationale": decision_rationale,
        "session_dialog": session_dialog,
        "decision_trace": raw.get("decision_trace")
        or {
            "planner": "llm" if planner_used else "rule_based",
            "decision": "direct_action",
            "action_type": action.get("type"),
            "verification_source": "RAM/structured GameState",
        },
        "expected_result": active_plan.get("repeat_until") or "single bounded action",
        "reason": raw.get("reason") or action.get("reason"),
    }


def _text_or_default(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _default_screen_description(state: dict[str, Any]) -> str:
    observation = state.get("observation", {})
    game_state = observation.get("state", {}) if isinstance(observation, dict) else {}
    mode = state.get("mode", game_state.get("mode", "unknown"))
    dialog = game_state.get("dialog") if isinstance(game_state.get("dialog"), dict) else {}
    dialog_text = dialog.get("text") or game_state.get("dialog_text")
    if dialog_text:
        return f"The current screen appears to be in {mode} mode with dialog text visible: {dialog_text!r}."
    return f"The current screen appears to be in {mode} mode with the latest RAM-derived state available."


def _default_current_location(state: dict[str, Any]) -> str:
    observation = state.get("observation", {})
    game_state = observation.get("state", {}) if isinstance(observation, dict) else {}
    map_info = game_state.get("map")
    map_name = game_state.get("map_name")
    if not map_name and isinstance(map_info, dict):
        map_name = map_info.get("name")
    position = game_state.get("position")
    parts = []
    if map_name:
        parts.append(f"map={map_name}")
    if position:
        parts.append(f"position={position}")
    return ", ".join(parts) if parts else "Current location is unknown from the available observation."


def _default_session_dialog(
    *,
    screen_description: str,
    current_location: str,
    current_goal: str,
    future_objective: str,
    decision_rationale: str,
) -> str:
    return (
        f"Screen: {screen_description} Location: {current_location} Current goal: {current_goal}. "
        f"Future objective: {future_objective} Decision rationale: {decision_rationale}"
    )

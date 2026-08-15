from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pokemon_agent.adk_agent.agents.memory_tools import (
    build_save_memory_tool,
)
from pokemon_agent.adk_agent.agents.interpreter.schema import ResultSummarizer
from pokemon_agent.adk_agent.agents.planner.agent import DEFAULT_ADK_MODEL
from pokemon_agent.adk_agent.agents.interpreter.prompt import RESULT_INTERPRETER_PROMPT
from pokemon_agent.adk_agent.agents.planner.schema import PokemonAgentState
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
from pokemon_agent.adk_agent.coordinator.action_cycle import should_interpret_action_outcome
from pokemon_agent.adk_agent.runtime.history import (
    RAW_HISTORY_TURNS,
    RESULT_INTERPRETER_PRIOR_TURNS,
    trim_session_to_recent_turns,
)
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_ADK_USER_ID,
    ContextFilteringSqliteSessionService,
)
from pokemon_agent.memory.file_memory import FileLongTermMemory


INTERPRETER_PROMPT = RESULT_INTERPRETER_PROMPT
LOGGER = logging.getLogger(__name__)
@dataclass
class ResultInterpreterAgent:
    summarizer: ResultSummarizer | None = None
    idle_pump: Callable[[], Any] | None = None
    idle_pump_interval: float = 1 / 30
    history_limit: int = RAW_HISTORY_TURNS
    trace: TraceSink | None = None
    name: str = "pokemon_red_result_interpreter_agent"

    def interpret(self, state: PokemonAgentState) -> PokemonAgentState:
        action_outcome = state.get("action_outcome", {})
        if not should_interpret_action_outcome(action_outcome):
            interpretation = dict(state.get("interpretation") or {})
            interpretation.setdefault("agent", self.name)
            interpretation.setdefault("phase", "result_interpretation")
            interpretation.setdefault("summary", "action_cycle_in_progress")
            interpretation["current_action_status"] = action_outcome.get("status", "continue")
            interpretation["current_state_changes"] = action_outcome.get("state_changes", [])
            interpretation["llm_called"] = False
            interpretation["history_compacted_turns"] = (
                1 if int(state.get("step_count", 0)) > self.history_limit else 0
            )
            interpretation.update(
                public_output_fields(
                    None,
                    dict(state),
                    default_thought=(
                        "The current action cycle is still in progress; wait for the next verifiable result."
                    ),
                )
            )
            emit_trace(
                self.trace,
                {
                    "agent": self.name,
                    "phase": "result_interpretation",
                    "step": state.get("step_count", 0),
                    "memory_written": [],
                },
            )
            return {"interpretation": interpretation, "interpret_error": None}

        payload = compact_interpreter_context(dict(state))
        summary_result: dict[str, Any] = {}
        interpret_error: str | None = None
        if self.summarizer is not None:
            supports_thinking_callback = hasattr(self.summarizer, "thinking_summary_callback")
            if supports_thinking_callback:
                self.summarizer.thinking_summary_callback = lambda summary: emit_trace(
                    self.trace,
                    {
                        "agent": self.name,
                        "phase": "result_interpretation_thinking",
                        "step": state.get("step_count", 0),
                        "thinking_summary": summary,
                    },
                )
            try:
                emit_trace(
                    self.trace,
                    {
                        "agent": self.name,
                        "phase": "result_interpretation_start",
                        "step": state.get("step_count", 0),
                    },
                )
                interpretation_wait_trace = None
                if not bool(getattr(self.summarizer, "stream_output", False)):
                    interpretation_wait_trace = lambda elapsed: emit_trace(
                        self.trace,
                        {
                            "agent": self.name,
                            "phase": "result_interpretation_wait",
                            "step": state.get("step_count", 0),
                            "elapsed_seconds": round(elapsed, 1),
                        },
                    )
                result = run_with_idle_pump(
                    lambda: self.summarizer.summarize(payload),
                    idle_pump=self.idle_pump,
                    idle_pump_interval=self.idle_pump_interval,
                    on_wait=interpretation_wait_trace,
                )
                if isinstance(result, dict):
                    summary_result = result
                else:
                    interpret_error = str(
                        getattr(self.summarizer, "last_interpret_error", None)
                        or "result_interpreter_returned_no_result"
                    )
            except Exception as exc:
                interpret_error = f"{type(exc).__name__}: {exc}"
            finally:
                if supports_thinking_callback:
                    self.summarizer.thinking_summary_callback = None

        written_keys = list(getattr(self.summarizer, "last_saved_memory_keys", []))
        summary_text = str(
            summary_result.get("summary")
            or summary_result.get("reason")
            or action_outcome.get("reason")
            or action_outcome.get("status")
            or "action_outcome_interpreted"
        )
        public_fields = public_output_fields(
            summary_result,
            dict(state),
            default_thought=summary_text,
        )
        interpretation = {
            "agent": self.name,
            "phase": "result_interpretation",
            "summary": summary_text,
            "action_succeeded": action_outcome.get("action_result") == "success",
            "action_status": action_outcome.get("status"),
            "goal_progress": summary_result.get("goal_progress"),
            "goal_completed": action_outcome.get("goal_completed", False),
            "verified_state_change": summary_result.get(
                "verified_state_change", action_outcome.get("state_changes", [])
            ),
            "failure_reason": summary_result.get(
                "failure_reason",
                ""
                if action_outcome.get("status") == "single_action_complete"
                else action_outcome.get("reason"),
            ),
            "important_event": action_outcome.get("important_event", False),
            "memory_written": written_keys,
            "llm_called": self.summarizer is not None and not bool(interpret_error),
            "history_compacted_turns": (
                1 if int(state.get("step_count", 0)) > self.history_limit else 0
            ),
            **public_fields,
        }
        emit_trace(
            self.trace,
            {
                "agent": self.name,
                "phase": (
                    "result_interpretation_done"
                    if self.summarizer is not None
                    else "result_interpretation"
                ),
                "step": state.get("step_count", 0),
                "thinking_summary": getattr(self.summarizer, "last_thinking_summary", None),
                "screen_description": interpretation["screen_description"],
                "current_location": interpretation["current_location"],
                "thought_summary": interpretation["thought_summary"],
                "memory_written": written_keys,
                "error": interpret_error,
            },
        )
        return {
            "interpretation": interpretation,
            "interpret_error": interpret_error,
            "interpreter_call_count": int(state.get("interpreter_call_count", 0))
            + int(interpretation["llm_called"]),
        }


def compact_interpreter_context(state: dict[str, Any]) -> dict[str, Any]:
    """Build the small, canonical state snapshot sent to the interpreter LLM."""
    observation = state.get("observation") if isinstance(state.get("observation"), dict) else {}
    game_state = observation.get("state") if isinstance(observation.get("state"), dict) else {}
    action_plan = state.get("active_action_plan") if isinstance(state.get("active_action_plan"), dict) else {}
    action_outcome = state.get("action_outcome") if isinstance(state.get("action_outcome"), dict) else {}
    state_diff = state.get("state_diff") if isinstance(state.get("state_diff"), dict) else {}
    execution_report = state.get("execution_report") if isinstance(state.get("execution_report"), dict) else {}
    execution_result = (
        execution_report.get("result") if isinstance(execution_report.get("result"), dict) else {}
    )

    last_result: dict[str, Any] = {
        "status": action_outcome.get("status", action_plan.get("status", "unknown")),
        "reason": str(action_outcome.get("reason") or "unknown")[:160],
        "position_changed": bool(
            state_diff.get("changes", {}).get("position")
            if isinstance(state_diff.get("changes"), dict)
            else "position_changed" in state_diff.get("event_types", [])
        ),
        "goal_completed": bool(action_outcome.get("goal_completed", False)),
    }
    movement_result = _movement_result_for_interpreter(action_plan, execution_report, execution_result)
    if movement_result:
        last_result["movement"] = movement_result

    transition_history = state.get("transition_history")
    transition = _last_transition(transition_history)
    compact_state: dict[str, Any] = {
        "map": game_state.get("map_name"),
        "pos": _position_list(game_state.get("position")),
        "mode": game_state.get("mode") or state.get("mode") or "unknown",
        "dialog": bool(game_state.get("dialog_open", False)),
        "battle": bool(game_state.get("in_battle", False)),
        "flags": _relevant_flags(game_state, action_plan, state.get("current_goal")),
        "party_count": _party_count(game_state),
    }
    dialog_text = game_state.get("dialog_text")
    if dialog_text:
        compact_state["dialog_text"] = " ".join(str(dialog_text).split())
    recent_dialog = _recent_dialog_text(transition_history)
    if recent_dialog and recent_dialog != compact_state.get("dialog_text"):
        compact_state["recent_dialog"] = recent_dialog
    party = _compact_party_for_memory(game_state.get("party"))
    if party:
        compact_state["party"] = party
    if compact_state["battle"]:
        battle = game_state.get("battle") if isinstance(game_state.get("battle"), dict) else {}
        opponent = battle.get("opponent") if isinstance(battle.get("opponent"), dict) else None
        if opponent is not None:
            compact_state["opponent"] = _compact_opponent_for_interpreter(opponent)

    return {
        "step": int(state.get("step_count", 0)),
        "state": compact_state,
        "action_plan": {
            "action": action_plan.get("action"),
            "status": action_plan.get("status"),
        },
        "last_result": last_result,
        "state_changes": _compact_state_changes(state_diff),
        "last_transition": transition,
    }


def _compact_opponent_for_interpreter(opponent: dict[str, Any]) -> dict[str, Any]:
    return {
        key: opponent.get(key)
        for key in ("species", "level", "hp", "max_hp", "status", "types")
        if opponent.get(key) is not None
    }


def _movement_result_for_interpreter(
    action_plan: dict[str, Any],
    execution_report: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, Any] | None:
    action = action_plan.get("action") if isinstance(action_plan.get("action"), dict) else {}
    if action.get("type") != "move":
        return None
    values = {
        "requested_target": action.get("target"),
        "requested_world_cell": execution_result.get("requested_world_cell"),
        "resolved_world_cell": execution_result.get("resolved_world_cell"),
        "target_out_of_visible_area": execution_result.get("target_out_of_visible_area"),
        "requested_target_reached": execution_result.get("requested_target_reached"),
        "resolved_target_reached": execution_result.get("resolved_target_reached"),
        "steps_taken": execution_result.get("steps_taken"),
        "navigation_replans": execution_result.get("navigation_replans"),
        "stop_reason": execution_report.get("stop_reason") or execution_result.get("stop_reason"),
    }
    return {key: value for key, value in values.items() if value is not None}


def _compact_state_changes(state_diff: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    seen: set[str] = set()
    events = state_diff.get("events") if isinstance(state_diff.get("events"), list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        field = _event_field(str(event.get("type") or ""))
        if not field or field in seen:
            continue
        changes.append(field)
        seen.add(field)

    changed_fields = state_diff.get("changes") if isinstance(state_diff.get("changes"), dict) else {}
    for raw_field, changed in changed_fields.items():
        field = {
            "map_id": "map",
            "dialog_open": "dialog",
            "in_battle": "battle",
        }.get(str(raw_field), str(raw_field))
        if not changed or field in seen:
            continue
        changes.append(field)
        seen.add(field)
    return changes


def _event_field(event_type: str) -> str:
    mappings = {
        "map_changed": "map",
        "warp": "map",
        "position_changed": "position",
        "dialog_opened": "dialog",
        "dialog_closed": "dialog",
        "dialog_text_changed": "dialog_text",
        "battle_started": "battle",
        "battle_ended": "battle",
        "menu_opened": "menu",
        "menu_closed": "menu",
        "item_obtained": "items",
        "items_changed": "items",
        "pokemon_obtained": "party",
        "party_changed": "party",
        "badges_changed": "badges",
        "event_flags_changed": "flags",
    }
    return mappings.get(event_type, event_type.removesuffix("_changed"))


def _last_transition(history: Any) -> dict[str, Any] | None:
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return None
    transition = history[-1]
    return {
        "result": transition.get("action_status"),
        "reason": str(transition.get("reason") or "unknown")[:160],
    }


def _recent_dialog_text(history: Any) -> str | None:
    if not isinstance(history, list):
        return None
    for transition in reversed(history[-2:]):
        if not isinstance(transition, dict):
            continue
        for state_name in ("after", "before"):
            snapshot = transition.get(state_name)
            if not isinstance(snapshot, dict) or not snapshot.get("dialog_text"):
                continue
            return " ".join(str(snapshot["dialog_text"]).split())
    return None


def _compact_party_for_memory(party: Any) -> list[dict[str, Any]]:
    if not isinstance(party, list):
        return []
    result: list[dict[str, Any]] = []
    for member in party[:6]:
        if not isinstance(member, dict):
            continue
        compact = {
            key: member.get(key)
            for key in ("species", "nickname", "level", "status")
            if member.get(key) not in (None, "")
        }
        if compact:
            result.append(compact)
    return result


def _relevant_flags(game_state: dict[str, Any], action_plan: dict[str, Any], goal: Any) -> dict[str, bool]:
    flags = game_state.get("flags") if isinstance(game_state.get("flags"), dict) else {}
    explicit: list[str] = []
    for condition in _iter_conditions(goal):
        if isinstance(condition, dict):
            path = str(condition.get("path") or "")
            if path.startswith("flags."):
                key = path.removeprefix("flags.")
                if key and key not in explicit:
                    explicit.append(key)

    action = action_plan.get("action") if isinstance(action_plan.get("action"), dict) else {}
    query = " ".join(
        str(value or "")
        for value in (
            action.get("type"),
            action.get("reason"),
            goal.get("id") if isinstance(goal, dict) else None,
            goal.get("description") if isinstance(goal, dict) else None,
        )
    )
    query_tokens = _tokens(query)
    selected = list(explicit)
    for key in flags:
        key_tokens = _tokens(str(key))
        if query_tokens.intersection(key_tokens) and key not in selected:
            selected.append(str(key))
    for key, value in flags.items():
        if value is True and key not in selected:
            selected.append(str(key))
    return {key: bool(flags[key]) for key in selected[:8] if key in flags}


def _iter_conditions(goal: Any) -> list[Any]:
    conditions: list[Any] = []
    if isinstance(goal, dict) and isinstance(goal.get("success_conditions"), list):
        conditions.extend(goal["success_conditions"])
    return conditions


def _position_list(position: Any) -> list[int] | None:
    if isinstance(position, dict) and position.get("x") is not None and position.get("y") is not None:
        return [int(position["x"]), int(position["y"])]
    if isinstance(position, (list, tuple)) and len(position) == 2:
        return [int(position[0]), int(position[1])]
    return None


def _party_count(game_state: dict[str, Any]) -> int:
    counts = game_state.get("counts") if isinstance(game_state.get("counts"), dict) else {}
    if counts.get("party") is not None:
        return int(counts["party"])
    party = game_state.get("party") if isinstance(game_state.get("party"), list) else []
    return len(party)


def _tokens(value: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in value)
    return {token for token in normalized.split() if len(token) >= 4}


@dataclass
class GoogleAdkResultInterpreter:
    model: str = DEFAULT_ADK_MODEL
    app_name: str = ADK_WEB_APP_NAME
    user_id: str = DEFAULT_ADK_USER_ID
    session_id: str = "pokemon-red-result-interpreter"
    temperature: float = 0.2
    max_output_tokens: int = 2048
    thinking_budget: int | None = -1
    stream_output: bool = True
    prior_session_turns: int = RESULT_INTERPRETER_PRIOR_TURNS
    session_db_path: str | os.PathLike[str] | None = None
    memory_store: FileLongTermMemory = field(default_factory=FileLongTermMemory)
    last_interpret_error: str | None = field(init=False, default=None)
    last_thinking_summary: str | None = field(init=False, default=None)
    thinking_summary_callback: Callable[[str], None] | None = field(init=False, default=None, repr=False)
    memory_activity_callback: Callable[[dict[str, Any]], None] | None = field(init=False, default=None, repr=False)
    memory_tool_activity: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        try:
            from google.adk.agents import Agent
            from google.adk.agents.run_config import RunConfig, StreamingMode
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
                prior_turn_limit=self.prior_session_turns,
            )
            if self.session_db_path is not None
            else InMemorySessionService()
        )
        self.agent = Agent(
            name="pokemon_red_result_interpreter_agent",
            model=self.model,
            description="Interprets Pokemon Red action outcomes and proposes durable memory facts.",
            instruction=INTERPRETER_PROMPT,
            generate_content_config=generate_config,
            tools=[
                build_save_memory_tool(
                    self.memory_store,
                    source="result_interpreter",
                    activity=self.memory_tool_activity,
                    on_activity=self._publish_memory_activity,
                ),
            ],
        )
        self.runner = Runner(
            agent=self.agent,
            app_name=self.app_name,
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
        thinking_budget: int | None = -1,
        stream_output: bool = True,
        session_db_path: str | os.PathLike[str] | None = None,
        memory_store: FileLongTermMemory | None = None,
    ) -> "GoogleAdkResultInterpreter":
        return cls(
            model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
            thinking_budget=thinking_budget,
            stream_output=stream_output,
            session_db_path=session_db_path,
            memory_store=memory_store or FileLongTermMemory(),
        )

    def summarize(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return asyncio.run(self.summarize_async(payload))

    async def summarize_async(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.last_interpret_error = None
        self.last_thinking_summary = None
        self.memory_tool_activity.clear()
        await self._ensure_session()
        trim_session_to_recent_turns(
            self.session_service,
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.session_id,
            max_turns=self.prior_session_turns,
        )
        from google.genai import types

        content = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
            ],
        )
        final_text = ""
        streamed_text = ""
        streamed_thinking = ""
        final_thinking = ""
        finish_reason: str | None = None
        console_stream = ConsoleTokenStream("pokemon_red_result_interpreter", enabled=self.stream_output)
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
            response_finish_reason = event_finish_reason(event)
            if response_finish_reason:
                finish_reason = response_finish_reason
            if event.is_final_response() and text:
                final_text = text

        if not final_text:
            final_text = streamed_text
        console_stream.finish(final_text)

        parsed = parse_json_object(final_text)
        if isinstance(parsed, dict):
            return parsed
        self.last_interpret_error = invalid_response_error(final_text, finish_reason=finish_reason)
        LOGGER.warning(
            "ADK result interpreter response rejected: %s; preview=%r",
            self.last_interpret_error,
            final_text[:500],
        )
        return None

    @property
    def last_saved_memory_keys(self) -> list[str]:
        return list(
            dict.fromkeys(
                str(key)
                for entry in self.memory_tool_activity
                if entry.get("tool") == "save_memory"
                for key in entry.get("keys", [])
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

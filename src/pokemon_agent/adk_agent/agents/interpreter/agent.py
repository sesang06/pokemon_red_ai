from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pokemon_agent.adk_agent.agents.interpreter.schema import ResultSummarizer
from pokemon_agent.adk_agent.agents.planner.agent import DEFAULT_ADK_MODEL
from pokemon_agent.adk_agent.agents.interpreter.prompt import RESULT_INTERPRETER_PROMPT
from pokemon_agent.adk_agent.agents.planner.schema import PokemonAgentState
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
from pokemon_agent.adk_agent.coordinator.action_cycle import (
    deterministic_memory_candidates,
    should_interpret_action_outcome,
)
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
INTERPRETER_MEMORY_LIMIT = 3


@dataclass
class MemoryConsolidator:
    memory_store: FileLongTermMemory

    def consolidate(
        self,
        candidates: list[dict[str, Any]],
        *,
        source: str = "memory_consolidator",
    ) -> list[str]:
        writes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            key = str(candidate.get("key") or "").strip()
            namespace = str(candidate.get("namespace") or "").strip().lower()
            if namespace and key and ":" not in key:
                key = f"{namespace}:{key}"
            if not key or key in seen or candidate.get("value") is None:
                continue
            seen.add(key)
            writes.append({"key": key, "value": candidate["value"]})
        return self.memory_store.remember_many(writes, source=source)


@dataclass
class ResultInterpreterAgent:
    memory_store: FileLongTermMemory
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
            emit_trace(
                self.trace,
                {
                    "agent": self.name,
                    "phase": "result_interpretation",
                    "step": state.get("step_count", 0),
                    "thought_summary": "No durable event or action-cycle boundary requires LLM interpretation.",
                    "memory_written": [],
                },
            )
            return {"interpretation": interpretation, "interpret_error": None}

        payload = compact_interpreter_context(dict(state))
        summary_result: dict[str, Any] = {}
        interpret_error: str | None = None
        if self.summarizer is not None:
            try:
                emit_trace(
                    self.trace,
                    {
                        "agent": self.name,
                        "phase": "result_interpretation_start",
                        "step": state.get("step_count", 0),
                        "thought_summary": (
                            "An action-cycle boundary or durable event was verified; interpret it once."
                        ),
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
                            "thought_summary": (
                                f"Result interpreter is evaluating the action outcome ({elapsed:.1f}s)."
                            ),
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

        model_candidates = summary_result.get("memory_candidates", summary_result.get("memory_writes", []))
        if isinstance(model_candidates, list) and model_candidates:
            candidates = [candidate for candidate in model_candidates if isinstance(candidate, dict)]
        else:
            candidates = deterministic_memory_candidates(dict(state))
        written_keys = MemoryConsolidator(self.memory_store).consolidate(candidates)
        summary_text = str(
            summary_result.get("summary")
            or summary_result.get("reason")
            or action_outcome.get("reason")
            or action_outcome.get("status")
            or "action_outcome_interpreted"
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
                if action_outcome.get("status") in {"condition_met", "single_action_complete"}
                else action_outcome.get("reason"),
            ),
            "important_event": action_outcome.get("important_event", False),
            "memory_written": written_keys,
            "llm_called": self.summarizer is not None and not bool(interpret_error),
            "history_compacted_turns": (
                1 if int(state.get("step_count", 0)) > self.history_limit else 0
            ),
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
                "thought_summary": summary_result.get("thought_summary") or summary_text,
                "decision_trace": summary_result.get("decision_trace"),
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
        "repeat_count": action_outcome.get("repeat_count"),
        "max_repeats": action_outcome.get("max_repeats"),
        "goal_completed": bool(action_outcome.get("goal_completed", False)),
    }
    movement_result = _movement_result_for_interpreter(action_plan, execution_report, execution_result)
    if movement_result:
        last_result["movement"] = movement_result

    transition = _last_transition(state.get("transition_history"))
    return {
        "step": int(state.get("step_count", 0)),
        "state": {
            "map": game_state.get("map_name"),
            "pos": _position_list(game_state.get("position")),
            "mode": game_state.get("mode") or state.get("mode") or "unknown",
            "dialog": bool(game_state.get("dialog_open", False)),
            "battle": bool(game_state.get("in_battle", False)),
            "flags": _relevant_flags(game_state, action_plan, state.get("current_goal")),
            "party_count": _party_count(game_state),
        },
        "action_plan": {
            "action": action_plan.get("action"),
            "repeat_until": action_plan.get("repeat_until"),
            "repeat_count": action_plan.get("repeat_count"),
            "max_repeats": action_plan.get("max_repeats"),
            "status": action_plan.get("status"),
        },
        "last_result": last_result,
        "state_changes": _compact_state_changes(state_diff),
        "last_transition": transition,
        "relevant_memory": _relevant_memory(
            state.get("long_term_memory"),
            map_name=game_state.get("map_name"),
            action_plan=action_plan,
        ),
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
        "steps_taken": execution_result.get("steps_taken"),
        "stop_reason": execution_report.get("stop_reason") or execution_result.get("stop_reason"),
    }
    return {key: value for key, value in values.items() if value is not None}


def _compact_state_changes(state_diff: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    events = state_diff.get("events") if isinstance(state_diff.get("events"), list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        field = _event_field(str(event.get("type") or ""))
        if not field or field in seen:
            continue
        entry: dict[str, Any] = {"field": field}
        before = _change_value(field, event.get("from"))
        after = _change_value(field, event.get("to"))
        if before is not None or after is not None:
            entry.update({"from": before, "to": after})
        changes.append(entry)
        seen.add(field)

    changed_fields = state_diff.get("changes") if isinstance(state_diff.get("changes"), dict) else {}
    before_state = state_diff.get("before") if isinstance(state_diff.get("before"), dict) else {}
    after_state = state_diff.get("after") if isinstance(state_diff.get("after"), dict) else {}
    for raw_field, changed in changed_fields.items():
        field = "map" if raw_field == "map_id" else str(raw_field)
        if not changed or field in seen:
            continue
        entry = {"field": field}
        before = _change_value(field, before_state.get("map_name") if field == "map" else before_state.get(raw_field))
        after = _change_value(field, after_state.get("map_name") if field == "map" else after_state.get(raw_field))
        if before is not None or after is not None:
            entry.update({"from": before, "to": after})
        changes.append(entry)
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


def _change_value(field: str, value: Any) -> Any:
    if field == "position":
        return _position_list(value)
    if field in {"flags", "items", "party", "badges", "warps", "dialog_text"}:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _last_transition(history: Any) -> dict[str, Any] | None:
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return None
    transition = history[-1]
    return {
        "result": transition.get("action_status"),
        "reason": str(transition.get("reason") or "unknown")[:160],
    }


def _relevant_flags(game_state: dict[str, Any], action_plan: dict[str, Any], goal: Any) -> dict[str, bool]:
    flags = game_state.get("flags") if isinstance(game_state.get("flags"), dict) else {}
    explicit: list[str] = []
    for condition in _iter_conditions(action_plan, goal):
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


def _iter_conditions(action_plan: dict[str, Any], goal: Any) -> list[Any]:
    conditions: list[Any] = []
    if isinstance(action_plan.get("repeat_until"), dict):
        conditions.append(action_plan["repeat_until"])
    if isinstance(goal, dict) and isinstance(goal.get("success_conditions"), list):
        conditions.extend(goal["success_conditions"])
    return conditions


def _relevant_memory(
    memory: Any,
    *,
    map_name: Any,
    action_plan: dict[str, Any],
) -> list[str]:
    if not isinstance(memory, dict) or not isinstance(memory.get("items"), dict):
        return []
    items = memory["items"]
    ordered_keys = memory.get("keys") if isinstance(memory.get("keys"), list) else list(items)
    action = action_plan.get("action") if isinstance(action_plan.get("action"), dict) else {}
    action_reason = str(action.get("reason") or action.get("type") or "").lower()
    query_tokens = _tokens(f"{map_name or ''} {action_reason}")
    ranked: list[tuple[int, int, str]] = []
    for index, raw_key in enumerate(ordered_keys):
        key = str(raw_key)
        item = items.get(key)
        if not isinstance(item, dict):
            continue
        searchable = f"{key} {item.get('value', '')}".lower()
        score = sum(1 for token in query_tokens if token in searchable)
        if map_name and key.lower() == f"map:{map_name}".lower():
            score += 20
        if action_reason and action_reason in key.lower():
            score += 20
        if key.lower().startswith(("failure:", "strategy:")):
            score += 4
        ranked.append((-score, index, key))

    values: list[str] = []
    for _score, _index, key in sorted(ranked):
        value = items[key].get("value")
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        text = " ".join(text.split())[:300]
        if text and text not in values:
            values.append(text)
        if len(values) >= INTERPRETER_MEMORY_LIMIT:
            break
    return values


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
    last_interpret_error: str | None = field(init=False, default=None)

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
            "responseMimeType": "application/json",
        }
        if self.thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinkingBudget=self.thinking_budget)
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

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        thinking_budget: int | None = -1,
        stream_output: bool = True,
        session_db_path: str | os.PathLike[str] | None = None,
    ) -> "GoogleAdkResultInterpreter":
        return cls(
            model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
            thinking_budget=thinking_budget,
            stream_output=stream_output,
            session_db_path=session_db_path,
        )

    def summarize(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return asyncio.run(self.summarize_async(payload))

    async def summarize_async(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.last_interpret_error = None
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
        finish_reason: str | None = None
        console_stream = ConsoleTokenStream("pokemon_red_result_interpreter", enabled=self.stream_output)
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

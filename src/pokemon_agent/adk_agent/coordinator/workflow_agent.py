from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps.app import App
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.genai import types
from pydantic import PrivateAttr
from typing_extensions import override

from pokemon_agent.adk_agent.agents.planner.schema import (
    DEFAULT_MAX_STEPS,
    PokemonAgentState,
    classify_mode,
)
from pokemon_agent.adk_agent.agents.goal import normalize_goal
from pokemon_agent.adk_agent.agents.interpreter.agent import compact_interpreter_context
from pokemon_agent.adk_agent.agents.planner.agent import parse_planner_response
from pokemon_agent.adk_agent.agents.shared import (
    emit_trace,
    event_finish_reason,
    event_text,
    invalid_response_error,
    parse_json_object,
    run_with_idle_pump,
)
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_ADK_USER_ID,
    ContextFilteringSqliteSessionService,
    build_events_compaction_config,
)

AUTOPLAY_SESSION_ID = "pokemon-red-autoplay"


@dataclass
class _NativeLlmResult:
    final_text: str = ""
    streamed_text: str = ""
    finish_reason: str | None = None
    error: str | None = None

    @property
    def response_text(self) -> str:
        return self.final_text or self.streamed_text

    def observe(self, event: Event) -> None:
        text = event_text(event)
        if getattr(event, "partial", False) and text:
            self.streamed_text += text
        elif text:
            self.final_text = text
        if event.is_final_response() and text:
            self.final_text = text
        finish_reason = event_finish_reason(event)
        if finish_reason:
            self.finish_reason = finish_reason


@dataclass
class _CapturedPlanner:
    decision: dict[str, Any] | None
    error: str | None
    memory_keys: list[str]
    stream_output: bool = True

    @property
    def last_plan_error(self) -> str | None:
        return self.error

    @property
    def last_memory_search_keys(self) -> list[str]:
        return list(self.memory_keys)

    def plan(self, _state: dict[str, Any]) -> dict[str, Any] | None:
        return self.decision


@dataclass
class _CapturedInterpreter:
    result: dict[str, Any] | None
    error: str | None
    memory_keys: list[str]
    goal_update: dict[str, Any] | None = None
    stream_output: bool = True

    @property
    def last_interpret_error(self) -> str | None:
        return self.error

    @property
    def last_saved_memory_keys(self) -> list[str]:
        return list(self.memory_keys)

    @property
    def last_goal_update(self) -> dict[str, Any] | None:
        return dict(self.goal_update) if self.goal_update is not None else None

    def summarize(self, _payload: dict[str, Any]) -> dict[str, Any] | None:
        return self.result


class PokemonExecutionAgent(BaseAgent):
    """Deterministic MCP execution and verification child."""

    @override
    async def _run_async_impl(self, ctx: InvocationContext):
        parent = self.parent_agent
        if not isinstance(parent, PokemonRedTeamAgent):
            raise RuntimeError(f"{self.name} must be a child of PokemonRedTeamAgent")

        summary = await asyncio.to_thread(parent.run_execution_phase)
        yield _event(ctx, author=self.name, payload=summary)


class PokemonRedTeamAgent(BaseAgent):
    """Runs the existing Pokemon loop as visible planning/execution/interpreter child agents."""

    _loop: PokemonAdkLoop = PrivateAttr()
    _main_goal: str | None = PrivateAttr()
    _max_steps: int | None = PrivateAttr()
    _checkpoint_every: int = PrivateAttr()
    _startup: Callable[[], Any] | None = PrivateAttr(default=None)
    _shutdown: Callable[[], Any] | None = PrivateAttr(default=None)
    _state: PokemonAgentState = PrivateAttr(default_factory=dict)
    _run_guard: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _native_planner_backend: Any | None = PrivateAttr(default=None)
    _native_interpreter_backend: Any | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        loop: PokemonAdkLoop,
        main_goal: str | None = None,
        max_steps: int | None = DEFAULT_MAX_STEPS,
        checkpoint_every: int = 10,
        startup: Callable[[], Any] | None = None,
        shutdown: Callable[[], Any] | None = None,
    ) -> None:
        planner_backend = loop.planning_agent.action_planner
        interpreter_backend = loop.result_interpreter_agent.summarizer
        planner_child = getattr(planner_backend, "agent", None)
        interpreter_child = getattr(interpreter_backend, "agent", None)
        native_planner = planner_backend if isinstance(planner_child, LlmAgent) else None
        native_interpreter = interpreter_backend if isinstance(interpreter_child, LlmAgent) else None
        if native_planner is None or native_interpreter is None:
            raise ValueError(
                "PokemonRedTeamAgent requires native ADK LlmAgent planner and interpreter backends"
            )
        super().__init__(
            name="pokemon_red_team",
            description=(
                "Runs Pokemon Red through visible planning, deterministic execution, "
                "and result interpretation phases."
            ),
            sub_agents=[
                planner_child,
                PokemonExecutionAgent(
                    name="pokemon_red_execution_agent",
                    description="Executes and verifies one validated game action.",
                ),
                interpreter_child,
            ],
        )
        self._loop = loop
        self._main_goal = main_goal
        self._max_steps = None if max_steps is None else max(0, int(max_steps))
        self._checkpoint_every = int(checkpoint_every)
        self._startup = startup
        self._shutdown = shutdown
        self._native_planner_backend = native_planner
        self._native_interpreter_backend = native_interpreter

    @property
    def final_state(self) -> PokemonAgentState:
        return dict(self._state)

    @override
    async def _run_async_impl(self, ctx: InvocationContext):
        if not self._run_guard.acquire(blocking=False):
            raise RuntimeError("Pokemon Red autoplay is already running in another Dev UI session")
        requested_steps = (
            self._max_steps
            if self._max_steps is not None
            else _requested_steps(ctx.user_content)
        )
        try:
            if self._startup is not None:
                await asyncio.to_thread(self._startup)
            self._state = self._loop.initialize_state(
                main_goal=self._main_goal,
                max_steps=requested_steps,
                checkpoint_every=self._checkpoint_every,
            )
            self._loop._publish(self._state, phase="starting")

            while not self._state.get("done", False):
                observed = await asyncio.to_thread(self._loop._observe, dict(self._state))
                self._state.update(observed)
                self._state["mode"] = classify_mode(self._state["observation"])
                self._loop._publish(self._state, phase="observed")
                yield _event(ctx, author=self.name, payload=_observation_summary(self._state))
                if self._state.get("done"):
                    break

                async for event in self._run_native_planning(ctx):
                    yield event
                if self._state.get("done"):
                    break

                # Execute against this team's state directly. ADK can rebind a
                # nested custom agent's parent while routing through the Web
                # coordinator, which made the execution child read a different
                # team instance and lose the freshly produced action plan.
                execution_summary = await asyncio.to_thread(self.run_execution_phase)
                yield _event(
                    ctx,
                    author=self.sub_agents[1].name,
                    payload=execution_summary,
                )

                async for event in self._run_native_interpretation(ctx):
                    yield event

            self._loop._publish(self._state, phase="completed")
            yield _event(
                ctx,
                author=self.name,
                payload=_completion_summary(self._state),
                is_output=True,
            )
        finally:
            try:
                if self._shutdown is not None:
                    await asyncio.to_thread(self._shutdown)
            finally:
                self._run_guard.release()

    async def _run_native_planning(self, ctx: InvocationContext):
        backend = self._native_planner_backend
        if backend is None:
            raise RuntimeError("native planner backend is unavailable")
        backend.memory_tool_activity.clear()
        result = _NativeLlmResult()
        content = backend._content_for_state(dict(self._state))
        try:
            async for event in self._stream_native_agent(
                ctx,
                self.sub_agents[0],
                content,
                result,
            ):
                yield event
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"

        raw_decision = parse_planner_response(result.response_text)
        if raw_decision is None and result.error is None:
            result.error = invalid_response_error(
                result.response_text,
                finish_reason=result.finish_reason,
            )
        captured = _CapturedPlanner(
            decision=raw_decision,
            error=result.error,
            memory_keys=backend.last_memory_search_keys,
        )
        original = self._loop.planning_agent.action_planner
        self._loop.planning_agent.action_planner = captured
        try:
            self._state.update(self._loop._plan(self._state))
        finally:
            self._loop.planning_agent.action_planner = original
        self._loop._publish(self._state, phase="planned")

    async def _run_native_interpretation(self, ctx: InvocationContext):
        backend = self._native_interpreter_backend
        if backend is None:
            raise RuntimeError("native interpreter backend is unavailable")
        backend.memory_tool_activity.clear()
        backend.goal_updates.begin(normalize_goal(self._state.get("goal")))
        result = _NativeLlmResult()
        content = _interpreter_content(dict(self._state))
        try:
            async for event in self._stream_native_agent(
                ctx,
                self.sub_agents[2],
                content,
                result,
            ):
                yield event
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"

        parsed = parse_json_object(result.response_text)
        if not isinstance(parsed, dict):
            parsed = None
            if result.error is None:
                result.error = invalid_response_error(
                    result.response_text,
                    finish_reason=result.finish_reason,
                )
        captured = _CapturedInterpreter(
            result=parsed,
            error=result.error,
            memory_keys=backend.last_saved_memory_keys,
            goal_update=backend.last_goal_update,
        )
        original = self._loop.result_interpreter_agent.summarizer
        self._loop.result_interpreter_agent.summarizer = captured
        try:
            self._state.update(self._loop._interpret(self._state))
        finally:
            self._loop.result_interpreter_agent.summarizer = original
        self._finish_interpretation_phase()

    async def _stream_native_agent(
        self,
        ctx: InvocationContext,
        agent: BaseAgent,
        content: types.Content,
        result: _NativeLlmResult,
    ):
        run_config = ctx.run_config
        previous_context = run_config.model_input_context
        run_config.model_input_context = [content]
        try:
            async for event in agent.run_async(ctx):
                result.observe(event)
                yield event
        finally:
            run_config.model_input_context = previous_context

    def _finish_interpretation_phase(self) -> None:
        self._loop._publish(self._state, phase="interpreted")
        self._state.update(self._loop._checkpoint(self._state))
        self._loop._publish(
            self._state,
            phase="completed" if self._state.get("done") else "checkpointed",
        )

    def run_execution_phase(self) -> dict[str, Any]:
        self._state.update(self._loop._act(self._state))
        self._state.update(self._loop._verify(self._state))
        self._loop._publish(self._state, phase="executed")
        return _execution_summary(self._state)


def _interpreter_content(state: PokemonAgentState) -> types.Content:
    payload = compact_interpreter_context(dict(state))
    parts = [
        types.Part.from_text(
            text=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
    ]
    screenshot = state.get("observation", {}).get("screenshot", {})
    screenshot_base64 = screenshot.get("base64") if isinstance(screenshot, dict) else None
    if screenshot_base64:
        parts.append(
            types.Part.from_bytes(
                data=base64.b64decode(screenshot_base64),
                mime_type="image/png",
                media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM,
            )
        )
    return types.Content(role="user", parts=parts)


def run_traced_pokemon_loop(
    loop: PokemonAdkLoop,
    *,
    main_goal: str | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    checkpoint_every: int = 10,
    session_db_path: str,
    idle_pump: Callable[[], Any] | None = None,
    idle_pump_interval: float = 1 / 30,
    event_sink: Callable[[Event], Any] | None = None,
) -> PokemonAgentState:
    """Run the normal loop under a traceable custom ADK agent hierarchy."""

    # The outer wait loop owns Qt/event pumping on the caller thread.
    loop.planning_agent.idle_pump = None
    loop.result_interpreter_agent.idle_pump = None
    root_agent = PokemonRedTeamAgent(
        loop=loop,
        main_goal=main_goal,
        max_steps=max_steps,
        checkpoint_every=checkpoint_every,
    )
    app = App(
        name=ADK_WEB_APP_NAME,
        root_agent=root_agent,
        events_compaction_config=build_events_compaction_config(),
    )
    session_service = ContextFilteringSqliteSessionService(
        session_db_path,
    )
    runner = Runner(app=app, session_service=session_service)

    async def run() -> PokemonAgentState:
        session = await session_service.get_session(
            app_name=ADK_WEB_APP_NAME,
            user_id=DEFAULT_ADK_USER_ID,
            session_id=AUTOPLAY_SESSION_ID,
        )
        if session is None:
            await session_service.create_session(
                app_name=ADK_WEB_APP_NAME,
                user_id=DEFAULT_ADK_USER_ID,
                session_id=AUTOPLAY_SESSION_ID,
            )
        request = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        f"Play Pokemon Red for at most {max_steps} steps."
                        + (f" Main goal: {main_goal}" if main_goal is not None else "")
                    )
                )
            ],
        )
        async for event in runner.run_async(
            user_id=DEFAULT_ADK_USER_ID,
            session_id=AUTOPLAY_SESSION_ID,
            new_message=request,
        ):
            if event_sink is not None:
                event_sink(event)
        return root_agent.final_state

    return run_with_idle_pump(
        lambda: asyncio.run(run()),
        idle_pump=idle_pump,
        idle_pump_interval=idle_pump_interval,
    )


def _event(
    ctx: InvocationContext,
    *,
    author: str,
    payload: dict[str, Any],
    is_output: bool = False,
) -> Event:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return Event(
        invocation_id=ctx.invocation_id,
        author=author,
        branch=ctx.branch,
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
        output=payload if is_output else None,
    )


def _observation_summary(state: PokemonAgentState) -> dict[str, Any]:
    game_state = state.get("observation", {}).get("state", {})
    return {
        "phase": "observed",
        "goal": normalize_goal(state.get("goal")),
        "step": int(state.get("step_count", 0)),
        "map": game_state.get("map_name"),
        "position": game_state.get("position"),
        "mode": state.get("mode"),
        "dialog_open": bool(game_state.get("dialog_open")),
        "in_battle": bool(game_state.get("in_battle")),
    }


def _execution_summary(state: PokemonAgentState) -> dict[str, Any]:
    result = state.get("action_result", {})
    outcome = state.get("action_outcome", {})
    plan = state.get("active_action_plan", {})
    return {
        "phase": "execution",
        "step": int(state.get("step_count", 0)),
        "action": plan.get("action") if isinstance(plan, dict) else None,
        "stop_reason": result.get("stop_reason"),
        "steps_taken": result.get("steps_taken"),
        "status": outcome.get("status"),
        "state_changes": outcome.get("state_changes", []),
    }


def _interpretation_summary(state: PokemonAgentState) -> dict[str, Any]:
    interpretation = state.get("interpretation", {})
    return {
        "phase": "interpretation",
        "goal": normalize_goal(state.get("goal")),
        "step": int(state.get("step_count", 0)),
        "summary": interpretation.get("summary"),
        "thought_summary": interpretation.get("thought_summary"),
        "memory_written": interpretation.get("memory_written", []),
        "error": state.get("interpret_error"),
    }


def _completion_summary(state: PokemonAgentState) -> dict[str, Any]:
    return {
        "phase": "completed",
        "goal": normalize_goal(state.get("goal")),
        "step": int(state.get("step_count", 0)),
        "done": bool(state.get("done")),
        "termination_reason": state.get("termination_reason"),
    }


def _requested_steps(content: types.Content | None, *, default: int = 100) -> int:
    text = " ".join(part.text or "" for part in (content.parts if content else [])).strip()
    matches = re.findall(r"(?<!\d)(\d{1,6})(?!\d)", text)
    if not matches:
        return default
    return max(1, min(int(matches[0]), DEFAULT_MAX_STEPS))

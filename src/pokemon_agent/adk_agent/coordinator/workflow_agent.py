from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable
from typing import Any, Literal

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
    DEFAULT_OBJECTIVE,
    PokemonAgentState,
    classify_mode,
)
from pokemon_agent.adk_agent.agents.shared import run_with_idle_pump
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_ADK_USER_ID,
    ContextFilteringSqliteSessionService,
)

AUTOPLAY_SESSION_ID = "pokemon-red-autoplay"
PhaseName = Literal["planning", "execution", "interpretation"]


class PokemonPhaseAgent(BaseAgent):
    """One visible ADK child span backed by an existing deterministic loop phase."""

    phase: PhaseName

    @override
    async def _run_async_impl(self, ctx: InvocationContext):
        parent = self.parent_agent
        if not isinstance(parent, PokemonRedTeamAgent):
            raise RuntimeError(f"{self.name} must be a child of PokemonRedTeamAgent")

        summary = await asyncio.to_thread(parent.run_phase, self.phase)
        yield _event(ctx, author=self.name, payload=summary)


class PokemonRedTeamAgent(BaseAgent):
    """Runs the existing Pokemon loop as visible planning/execution/interpreter child agents."""

    _loop: PokemonAdkLoop = PrivateAttr()
    _objective: str = PrivateAttr()
    _max_steps: int | None = PrivateAttr()
    _checkpoint_every: int = PrivateAttr()
    _startup: Callable[[], Any] | None = PrivateAttr(default=None)
    _shutdown: Callable[[], Any] | None = PrivateAttr(default=None)
    _state: PokemonAgentState = PrivateAttr(default_factory=dict)
    _run_guard: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def __init__(
        self,
        *,
        loop: PokemonAdkLoop,
        objective: str = DEFAULT_OBJECTIVE,
        max_steps: int | None = DEFAULT_MAX_STEPS,
        checkpoint_every: int = 10,
        startup: Callable[[], Any] | None = None,
        shutdown: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(
            name="pokemon_red_team",
            description=(
                "Runs Pokemon Red through visible planning, deterministic execution, "
                "and result interpretation phases."
            ),
            sub_agents=[
                PokemonPhaseAgent(
                    name="pokemon_red_planning_agent",
                    description="Produces one bounded action plan.",
                    phase="planning",
                ),
                PokemonPhaseAgent(
                    name="pokemon_red_execution_agent",
                    description="Executes and verifies one validated game action.",
                    phase="execution",
                ),
                PokemonPhaseAgent(
                    name="pokemon_red_result_interpreter_agent",
                    description="Interprets the verified result and updates durable memory.",
                    phase="interpretation",
                ),
            ],
        )
        self._loop = loop
        self._objective = str(objective or DEFAULT_OBJECTIVE)
        self._max_steps = None if max_steps is None else max(0, int(max_steps))
        self._checkpoint_every = int(checkpoint_every)
        self._startup = startup
        self._shutdown = shutdown

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
                objective=self._objective,
                max_steps=requested_steps,
                checkpoint_every=self._checkpoint_every,
            )
            self._loop._publish(self._state, phase="starting")

            while not self._state.get("done", False):
                observed = await asyncio.to_thread(self._loop._observe, dict(self._state))
                self._state.update(observed)
                self._state["mode"] = classify_mode(self._state["observation"])
                self._state.update(self._loop._verify_goal_before_action(self._state))
                self._loop._publish(self._state, phase="observed")
                yield _event(ctx, author=self.name, payload=_observation_summary(self._state))
                if self._state.get("done"):
                    break

                async for event in self.sub_agents[0].run_async(ctx):
                    yield event
                if self._state.get("done"):
                    break

                async for event in self.sub_agents[1].run_async(ctx):
                    yield event

                async for event in self.sub_agents[2].run_async(ctx):
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

    def run_phase(self, phase: PhaseName) -> dict[str, Any]:
        if phase == "planning":
            self._state.update(self._loop._plan(self._state))
            self._loop._publish(self._state, phase="planned")
            return _planning_summary(self._state)

        if phase == "execution":
            self._state.update(self._loop._act(self._state))
            self._state.update(self._loop._verify(self._state))
            self._loop._publish(self._state, phase="executed")
            return _execution_summary(self._state)

        self._state.update(self._loop._interpret(self._state))
        self._loop._publish(self._state, phase="interpreted")
        self._state.update(self._loop._checkpoint(self._state))
        self._loop._publish(
            self._state,
            phase="completed" if self._state.get("done") else "checkpointed",
        )
        return _interpretation_summary(self._state)


def run_traced_pokemon_loop(
    loop: PokemonAdkLoop,
    *,
    objective: str = DEFAULT_OBJECTIVE,
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
        objective=objective,
        max_steps=max_steps,
        checkpoint_every=checkpoint_every,
    )
    app = App(name=ADK_WEB_APP_NAME, root_agent=root_agent)
    session_service = ContextFilteringSqliteSessionService(
        session_db_path,
        prior_turn_limit=None,
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
                    text=f"Play Pokemon Red for at most {max_steps} steps. Objective: {objective}"
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
        "step": int(state.get("step_count", 0)),
        "map": game_state.get("map_name"),
        "position": game_state.get("position"),
        "mode": state.get("mode"),
        "dialog_open": bool(game_state.get("dialog_open")),
        "in_battle": bool(game_state.get("in_battle")),
    }


def _planning_summary(state: PokemonAgentState) -> dict[str, Any]:
    decision = state.get("plan_decision") if isinstance(state.get("plan_decision"), dict) else {}
    return {
        "phase": "planning",
        "step": int(state.get("step_count", 0)),
        "action": state.get("planned_action"),
        "thought_summary": decision.get("thought_summary"),
        "memory_keys_read": decision.get("memory_keys_read", []),
        "plan_error": state.get("plan_error"),
    }


def _execution_summary(state: PokemonAgentState) -> dict[str, Any]:
    result = state.get("action_result", {})
    outcome = state.get("action_outcome", {})
    return {
        "phase": "execution",
        "step": int(state.get("step_count", 0)),
        "action": state.get("planned_action"),
        "stop_reason": result.get("stop_reason"),
        "steps_taken": result.get("steps_taken"),
        "status": outcome.get("status"),
        "state_changes": outcome.get("state_changes", []),
    }


def _interpretation_summary(state: PokemonAgentState) -> dict[str, Any]:
    interpretation = state.get("interpretation", {})
    return {
        "phase": "interpretation",
        "step": int(state.get("step_count", 0)),
        "summary": interpretation.get("summary"),
        "thought_summary": interpretation.get("thought_summary"),
        "memory_written": interpretation.get("memory_written", []),
        "error": state.get("interpret_error"),
    }


def _completion_summary(state: PokemonAgentState) -> dict[str, Any]:
    return {
        "phase": "completed",
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

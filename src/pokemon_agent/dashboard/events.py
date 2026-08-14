from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from pokemon_agent.dashboard.models import (
    initial_live_state,
    memory_recent,
    now_iso,
    observation_state,
    runtime_state,
    state_event_record,
)


@dataclass(frozen=True)
class LiveSubscription:
    identifier: int
    queue: asyncio.Queue[dict[str, Any]]


@dataclass
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


class LiveEventHub:
    """Thread-safe state/event bridge from PyBoy and ADK to WebSocket clients."""

    def __init__(self, *, event_limit: int = 500, state_hz: float = 10.0) -> None:
        self.event_limit = max(20, int(event_limit))
        self.state_interval = 1.0 / max(1.0, min(float(state_hz), 30.0))
        self._lock = threading.RLock()
        self._state = initial_live_state()
        self._last_emitted_state = copy.deepcopy(self._state)
        self._events: deque[dict[str, Any]] = deque(maxlen=self.event_limit)
        self._subscribers: dict[int, _Subscriber] = {}
        self._next_subscriber_id = 1
        self._revision = 0
        self._next_event_id = 1
        self._last_state_emit = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "kind": "snapshot",
                "revision": self._revision,
                "state": copy.deepcopy(self._state),
                "events": list(copy.deepcopy(self._events)),
            }

    def subscribe(self) -> LiveSubscription:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        with self._lock:
            identifier = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[identifier] = _Subscriber(loop=loop, queue=queue)
            queue.put_nowait(self.snapshot())
        return LiveSubscription(identifier=identifier, queue=queue)

    def unsubscribe(self, subscription: LiveSubscription) -> None:
        with self._lock:
            self._subscribers.pop(subscription.identifier, None)

    def publish_observation(
        self,
        observation: dict[str, Any],
        *,
        ticker: dict[str, Any] | None = None,
    ) -> None:
        update = observation_state(observation, ticker=ticker)
        state_events = list(update.get("debug", {}).get("state_events") or [])
        with self._lock:
            self._merge_state(update)
            for raw_event in state_events:
                if not isinstance(raw_event, dict):
                    continue
                if raw_event.get("type") == "dialog_text_changed":
                    continue
                event_type, message, payload = state_event_record(raw_event)
                self._append_event(event_type, message, payload, source="game")
            self._emit_state_if_due(force=False)

    def publish_runtime(self, state: dict[str, Any], *, phase: str) -> None:
        update = runtime_state(state, phase=phase)
        with self._lock:
            self._merge_state(update)
            if phase == "executed":
                outcome = state.get("action_outcome") if isinstance(state.get("action_outcome"), dict) else {}
                status = str(outcome.get("status") or "unknown")
                passed = status == "single_action_complete"
                self._append_event(
                    "VERIFICATION_PASSED" if passed else "VERIFICATION_FAILED",
                    f"Verifier: {status.replace('_', ' ')}",
                    {
                        "action": state.get("planned_action"),
                        "outcome": outcome,
                        "state_diff": state.get("state_diff"),
                    },
                    source="verifier",
                )
            if phase == "completed" and state.get("termination_reason") == "goal_completed":
                self._append_event(
                    "GOAL_COMPLETED",
                    f"Goal completed: {state.get('objective')}",
                    {"goal": state.get("current_goal")},
                    source="verifier",
                )
            self._emit_state_if_due(force=True)

    def publish_trace(self, trace: dict[str, Any]) -> None:
        phase = str(trace.get("phase") or "unknown")
        with self._lock:
            if phase in {"planning_thinking", "result_interpretation_thinking"}:
                self._update_thinking(trace, status="streaming", append_event=False)
            elif phase == "planning_done":
                self._update_thinking(trace, status="complete", append_event=True)
                action_plan = trace.get("action_plan") if isinstance(trace.get("action_plan"), dict) else {}
                action = action_plan.get("action") if isinstance(action_plan.get("action"), dict) else {}
                self._append_event(
                    "PLAN_CREATED",
                    _action_message("Plan", action),
                    _planner_event_payload(trace, action_plan),
                    source="planner",
                )
                all_keys = trace.get("memory_keys_read") if isinstance(trace.get("memory_keys_read"), list) else []
                keys = all_keys[:8]
                if keys:
                    self._append_event(
                        "MEMORY_READ",
                        f"Read {len(all_keys)} relevant memory entr{'y' if len(all_keys) == 1 else 'ies'}",
                        {"keys": keys, "total_count": len(all_keys)},
                        source="memory",
                    )
                    self._state["memory"]["last_activity"] = {"type": "read", "keys": keys, "at": now_iso()}
            elif phase == "execution_done":
                action = trace.get("action") if isinstance(trace.get("action"), dict) else {}
                self._append_event(
                    "ACTION_EXECUTED",
                    _action_message("Executed", action),
                    _execution_event_payload(trace),
                    source="executor",
                )
            elif phase in {"result_interpretation", "result_interpretation_done"}:
                self._update_thinking(trace, status="complete", append_event=True)
                self._append_event(
                    "RESULT_INTERPRETED",
                    "Action result interpreted",
                    _interpreter_event_payload(trace),
                    source="interpreter",
                )
                keys = trace.get("memory_written") if isinstance(trace.get("memory_written"), list) else []
                if keys:
                    self._append_event(
                        "MEMORY_UPDATED",
                        f"Updated {len(keys)} memory entr{'y' if len(keys) == 1 else 'ies'}",
                        {"keys": keys},
                        source="memory",
                    )
                    self._state["memory"]["last_activity"] = {"type": "write", "keys": keys, "at": now_iso()}
            elif phase.endswith("error") or trace.get("error"):
                self._append_event(
                    "ERROR",
                    str(trace.get("error") or f"{phase} failed"),
                    _json_safe(trace),
                    source=str(trace.get("agent") or "agent"),
                )
            # Thinking summaries reuse one state slot. Push every streamed chunk
            # immediately; only the completed summary is appended to the event log.
            self._emit_state_if_due(force=True)

    def _update_thinking(
        self,
        trace: dict[str, Any],
        *,
        status: str,
        append_event: bool,
    ) -> None:
        summary = str(trace.get("thinking_summary") or "").strip()
        if not summary:
            return
        phase = str(trace.get("phase") or "")
        role = "interpreter" if phase.startswith("result_interpretation") else "planner"
        updated_at = now_iso()
        self._state["agent"]["thinking"] = {
            "agent": role,
            "status": status,
            "summary": summary,
            "updated_at": updated_at,
        }
        if append_event:
            self._append_event(
                "THINKING_SUMMARY",
                f"{role.title()} thinking summary",
                {
                    "agent": role,
                    "step": trace.get("step"),
                    "summary": summary,
                    "status": status,
                },
                source=role,
            )

    def publish_memory_snapshot(self, items: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self._state["memory"]["recent"] = memory_recent(items)
            self._state["updated_at"] = now_iso()
            self._emit_state_if_due(force=True)

    def publish_system_event(self, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._append_event(event_type, message, payload or {}, source="dashboard")
            self._emit_state_if_due(force=True)

    def _merge_state(self, update: dict[str, Any]) -> None:
        _deep_merge(self._state, _json_safe(update))
        self._state["updated_at"] = update.get("updated_at") or now_iso()

    def _append_event(self, event_type: str, message: str, payload: Any, *, source: str) -> None:
        event = {
            "id": self._next_event_id,
            "timestamp": now_iso(),
            "type": event_type,
            "source": source,
            "message": message,
            "payload": _json_safe(payload),
        }
        self._next_event_id += 1
        self._events.append(event)
        self._broadcast({"kind": "event", "event": event})

    def _emit_state_if_due(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_state_emit < self.state_interval:
            return
        changes = _state_delta(self._state, self._last_emitted_state)
        if not changes:
            return
        self._revision += 1
        self._last_state_emit = now
        self._last_emitted_state = copy.deepcopy(self._state)
        self._broadcast({"kind": "state_delta", "revision": self._revision, "changes": changes})

    def _broadcast(self, message: dict[str, Any]) -> None:
        for subscriber in tuple(self._subscribers.values()):
            subscriber.loop.call_soon_threadsafe(_offer, subscriber.queue, copy.deepcopy(message))


def _offer(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(message)


def _deep_merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _state_delta(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, value in current.items():
        old_value = previous.get(key)
        if isinstance(value, dict) and isinstance(old_value, dict):
            nested = _state_delta(value, old_value)
            if nested:
                changes[key] = nested
        elif key not in previous or old_value != value:
            changes[key] = copy.deepcopy(value)
    return changes


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _action_message(prefix: str, action: dict[str, Any]) -> str:
    action_type = str(action.get("type") or "action").upper()
    if action_type == "MOVE":
        target = action.get("target")
        return f"{prefix} MOVE to {target}"
    if action_type == "BUTTONS":
        return f"{prefix} BUTTONS {action.get('buttons') or []}"
    return f"{prefix} {action_type}"


def _planner_event_payload(trace: dict[str, Any], action_plan: dict[str, Any]) -> dict[str, Any]:
    """Expose the action contract and explicitly public context fields."""
    return _without_none(
        {
            "step": trace.get("step"),
            "screen_description": trace.get("screen_description"),
            "current_location": trace.get("current_location"),
            "thought_summary": trace.get("thought_summary"),
            "decision": action_plan.get("action"),
            "memory_keys_read": (
                trace.get("memory_keys_read", [])[:8]
                if isinstance(trace.get("memory_keys_read"), list)
                else []
            ),
            "error": trace.get("error"),
        }
    )


def _execution_event_payload(trace: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "step": trace.get("step"),
            "action": trace.get("action"),
            "stop_reason": trace.get("stop_reason"),
            "success_hint": trace.get("success_hint"),
            "error": trace.get("error"),
        }
    )


def _interpreter_event_payload(trace: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "step": trace.get("step"),
            "screen_description": trace.get("screen_description"),
            "current_location": trace.get("current_location"),
            "thought_summary": trace.get("thought_summary"),
            "memory_written": trace.get("memory_written"),
            "error": trace.get("error"),
        }
    )


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in values.items() if value is not None}

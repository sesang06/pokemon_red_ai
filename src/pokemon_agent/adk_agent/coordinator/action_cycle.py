from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, TypedDict

from pokemon_agent.adk_agent.runtime.history import RAW_HISTORY_TURNS


IMPORTANT_EVENT_TYPES = {
    "map_changed",
    "warp",
    "battle_started",
    "battle_ended",
    "dialog_opened",
    "dialog_closed",
    "dialog_text_changed",
    "menu_opened",
    "menu_closed",
    "item_obtained",
    "items_changed",
    "pokemon_obtained",
    "party_changed",
    "badges_changed",
    "event_flags_changed",
}
HARD_STOP_REASONS = {
    "controls_locked",
    "execution_error",
    "movement_blocked",
    "no_path",
    "realtime_ticker_stopped",
}


class StateDiff(TypedDict, total=False):
    events: list[dict[str, Any]]
    event_types: list[str]
    important_events: list[str]
    changes: dict[str, bool]
    before: dict[str, Any]
    after: dict[str, Any]
    meaningful: bool


def verify_action_cycle(
    action_plan: dict[str, Any],
    *,
    action_result: dict[str, Any],
    state_diff: StateDiff,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = deepcopy(action_plan)
    stop_reason = str(action_result.get("stop_reason") or "")
    execution_failed = bool(action_result.get("error")) or stop_reason == "execution_error"
    interrupted = stop_reason in HARD_STOP_REASONS

    if execution_failed:
        status = "execution_error"
    elif interrupted:
        status = "interrupted"
    else:
        status = "single_action_complete"

    updated["status"] = status
    durable_event = any(
        event_type
        in {
            "map_changed",
            "warp",
            "item_obtained",
            "pokemon_obtained",
            "badges_changed",
            "event_flags_changed",
        }
        for event_type in state_diff.get("important_events", [])
    )
    outcome = {
        "status": status,
        "action_result": "failed" if execution_failed or interrupted else "success",
        "state_changes": list(state_diff.get("event_types", [])),
        "state_changed": bool(state_diff.get("meaningful")),
        "important_event": durable_event,
        "reason": _action_outcome_reason(status, stop_reason),
    }
    return updated, outcome


def build_state_diff(before_observation: dict[str, Any], after_observation: dict[str, Any]) -> StateDiff:
    before = before_observation.get("state", {}) if isinstance(before_observation, dict) else {}
    after = after_observation.get("state", {}) if isinstance(after_observation, dict) else {}
    keys = (
        "map_id",
        "position",
        "items",
        "party",
        "badges",
        "money",
        "pokedex_caught",
        "dialog_open",
        "dialog_text",
        "in_battle",
        "mode",
        "warps",
    )
    changes = {key: before.get(key) != after.get(key) for key in keys}
    changes["flags"] = _durable_flags(before.get("flags")) != _durable_flags(after.get("flags"))

    events = [
        dict(event)
        for event in after_observation.get("state_events", [])
        if isinstance(event, dict) and event.get("type") != "initial_observation"
    ]
    existing = {str(event.get("type")) for event in events}
    _add_boolean_transition(events, existing, before, after, "dialog_open", "dialog_opened", "dialog_closed")
    _add_boolean_transition(events, existing, before, after, "in_battle", "battle_started", "battle_ended")
    _add_boolean_transition(events, existing, _menu_state(before), _menu_state(after), "active", "menu_opened", "menu_closed")
    if changes["map_id"] and "map_changed" not in existing:
        events.append({"type": "map_changed", "from": before.get("map_name"), "to": after.get("map_name")})
    if changes["map_id"] and "warp" not in existing:
        events.append({"type": "warp", "from": before.get("map_name"), "to": after.get("map_name")})
    if changes["position"] and "position_changed" not in existing:
        events.append({"type": "position_changed", "from": before.get("position"), "to": after.get("position")})
    if changes["dialog_text"] and after.get("dialog_open") and "dialog_text_changed" not in existing:
        events.append({"type": "dialog_text_changed", "from": before.get("dialog_text"), "to": after.get("dialog_text")})
    if changes["items"]:
        if "item_obtained" not in existing:
            for name, quantity in _positive_item_deltas(before, after).items():
                events.append({"type": "item_obtained", "item": name, "quantity": quantity})
        if "items_changed" not in existing:
            events.append({"type": "items_changed", "from": before.get("items", []), "to": after.get("items", [])})
    if changes["party"] and "pokemon_obtained" not in existing and len(after.get("party") or []) > len(before.get("party") or []):
        events.append({"type": "pokemon_obtained", "party_size": len(after.get("party") or [])})
    if changes["flags"] and "event_flags_changed" not in existing:
        events.append({"type": "event_flags_changed", "from": before.get("flags", {}), "to": after.get("flags", {})})

    event_types = list(dict.fromkeys(str(event.get("type")) for event in events if event.get("type")))
    important = [event_type for event_type in event_types if event_type in IMPORTANT_EVENT_TYPES]
    return {
        "events": events,
        "event_types": event_types,
        "important_events": important,
        "changes": changes,
        "before": _compact_game_state(before),
        "after": _compact_game_state(after),
        "meaningful": any(changes.values()) or bool(event_types),
    }


def action_transition_summary(
    action: dict[str, Any],
    state_diff: StateDiff,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action": action,
        "before": state_diff.get("before"),
        "after": state_diff.get("after"),
        "state_changes": state_diff.get("event_types", []),
        "action_status": outcome.get("status"),
        "reason": outcome.get("reason"),
    }


def append_transition(
    history: list[dict[str, Any]],
    transition: dict[str, Any],
    *,
    existing_summary: Any = None,
    limit: int = RAW_HISTORY_TURNS,
) -> tuple[list[dict[str, Any]], str | None]:
    combined = [*history, transition]
    overflow = combined[:-limit] if len(combined) > limit else []
    recent = combined[-limit:]
    if not overflow:
        return recent, str(existing_summary) if existing_summary else None

    result_counts: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    for entry in overflow:
        result = str(entry.get("action_status") or "unknown")
        result_counts[result] = result_counts.get(result, 0) + 1
        for event_type in entry.get("state_changes", []):
            event_name = str(event_type)
            event_counts[event_name] = event_counts.get(event_name, 0) + 1
    line = (
        f"Compacted {len(overflow)} state transition(s); "
        f"action_results={result_counts}; state_events={event_counts or {'none': len(overflow)}}."
    )
    previous = [part for part in str(existing_summary or "").splitlines() if part.strip()]
    previous.append(line)
    return recent, "\n".join(previous[-RAW_HISTORY_TURNS:])


def _action_outcome_reason(status: str, stop_reason: str) -> str:
    return stop_reason or status


def _menu_state(state: dict[str, Any]) -> dict[str, Any]:
    menu = state.get("menu")
    if isinstance(menu, dict):
        return menu
    return {"active": state.get("mode") == "inventory"}


def _add_boolean_transition(
    events: list[dict[str, Any]],
    existing: set[str],
    before: dict[str, Any],
    after: dict[str, Any],
    key: str,
    opened: str,
    closed: str,
) -> None:
    old = bool(before.get(key))
    new = bool(after.get(key))
    event_type = opened if not old and new else closed if old and not new else None
    if event_type and event_type not in existing:
        events.append({"type": event_type})
        existing.add(event_type)


def _item_counts(state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = _normalize_item_name(str(item.get("name") or item.get("item_id") or "unknown"))
        counts[name] = counts.get(name, 0) + int(item.get("quantity") or 0)
    return counts


def _positive_item_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    old = _item_counts(before)
    new = _item_counts(after)
    return {name: quantity - old.get(name, 0) for name, quantity in new.items() if quantity > old.get(name, 0)}


def _normalize_item_name(value: str) -> str:
    normalized = value.strip().lower().replace("'s", "s")
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _compact_game_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "map_id": state.get("map_id"),
        "map_name": state.get("map_name"),
        "position": state.get("position"),
        "mode": state.get("mode"),
        "in_battle": state.get("in_battle"),
        "dialog_open": state.get("dialog_open"),
        "dialog_text": state.get("dialog_text"),
        "items": state.get("items", []),
        "party": state.get("party", []),
        "pokedex_caught": state.get("pokedex_caught"),
        "badges": state.get("badges", []),
        "flags": _durable_flags(state.get("flags")),
    }


def _durable_flags(flags: Any) -> dict[str, Any]:
    if not isinstance(flags, dict):
        return {}
    return {str(key): value for key, value in flags.items() if not str(key).startswith("has_")}

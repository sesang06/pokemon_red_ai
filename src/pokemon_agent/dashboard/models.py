from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


EVENT_TYPE_BY_STATE_EVENT = {
    "initial_observation": "OBSERVE_COMPLETED",
    "map_changed": "MAP_CHANGED",
    "warp": "MAP_CHANGED",
    "position_changed": "STATE_CHANGED",
    "dialog_opened": "DIALOG_OPENED",
    "dialog_closed": "DIALOG_CLOSED",
    "dialog_text_changed": "STATE_CHANGED",
    "battle_started": "BATTLE_STARTED",
    "battle_ended": "BATTLE_ENDED",
    "menu_opened": "STATE_CHANGED",
    "menu_closed": "STATE_CHANGED",
    "item_obtained": "ITEM_OBTAINED",
    "pokemon_obtained": "POKEMON_OBTAINED",
    "event_flags_changed": "STATE_CHANGED",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def initial_live_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "emulator": {
            "status": "waiting",
            "frame_index": 0,
            "tool_step_index": 0,
            "fps": None,
            "ticker_alive": False,
        },
        "game": {
            "map_id": None,
            "map_name": "Waiting for game",
            "position": None,
            "facing": None,
            "mode": "unknown",
            "dialog_open": False,
            "dialog_text": None,
            "in_battle": False,
            "party": [],
            "items": [],
            "badges": [],
            "money": None,
            "screenshot": None,
            "overlay": None,
        },
        "agent": {
            "phase": "not_started",
            "objective": None,
            "task": None,
            "action": None,
            "result": None,
            "pipeline": pipeline_for_phase("not_started"),
            "planner_calls": 0,
            "executor_actions": 0,
            "interpreter_calls": 0,
            "plan_error": None,
            "interpret_error": None,
            "thinking": {
                "agent": None,
                "status": "idle",
                "summary": None,
                "updated_at": None,
            },
        },
        "navigation": {
            "player": None,
            "target": None,
            "path": [],
            "visible_cells": [],
            "walk_area_collision": [],
            "world_map": None,
        },
        "memory": {"recent": [], "last_activity": None},
        "debug": {
            "state_events": [],
            "state_diff": None,
            "action_outcome": None,
            "ram": {},
            "screenshot_metadata": None,
        },
    }


def observation_state(observation: dict[str, Any], *, ticker: dict[str, Any] | None = None) -> dict[str, Any]:
    game_state = observation.get("state") if isinstance(observation.get("state"), dict) else {}
    screenshot = _image_payload(observation.get("screenshot"))
    overlay = _image_payload(observation.get("screenshot_overlay"))
    ticker = ticker or {}
    position = _position(game_state.get("position"))
    return {
        "updated_at": now_iso(),
        "emulator": {
            "status": "running" if ticker.get("ticker_alive", True) else "paused",
            "frame_index": int(observation.get("frame_index", 0)),
            "tool_step_index": int(observation.get("tool_step_index", 0)),
            "fps": ticker.get("fps"),
            "snapshot_hz": ticker.get("snapshot_hz"),
            "ticker_alive": bool(ticker.get("ticker_alive", True)),
            "ticker_error": ticker.get("ticker_error"),
        },
        "game": {
            "map_id": game_state.get("map_id"),
            "map_name": game_state.get("map_name") or "Unknown",
            "position": position,
            "facing": game_state.get("facing"),
            "mode": game_state.get("mode") or "unknown",
            "dialog_open": bool(game_state.get("dialog_open")),
            "dialog_text": game_state.get("dialog_text"),
            "in_battle": bool(game_state.get("in_battle")),
            "party": [_party_member(member) for member in _dict_list(game_state.get("party"))],
            "items": [_item(item) for item in _dict_list(game_state.get("items"))],
            "badges": list(game_state.get("badges") or []),
            "money": game_state.get("money"),
            "game_time": game_state.get("game_time"),
            "screenshot": screenshot,
            "overlay": overlay,
        },
        "navigation": {
            "player": position,
            "visible_cells": observation.get("visible_world_cells") or [],
            "walk_area_collision": observation.get("walk_area_collision") or [],
            "world_map": observation.get("world_map"),
        },
        "debug": {
            "state_events": observation.get("state_events") or game_state.get("events") or [],
            "ram": observation.get("ram") or {},
            "screenshot_metadata": {
                "frame": None if screenshot is None else _without_base64(screenshot),
                "overlay": None if overlay is None else _without_base64(overlay),
            },
        },
    }


def runtime_state(state: dict[str, Any], *, phase: str) -> dict[str, Any]:
    goal = state.get("current_goal") if isinstance(state.get("current_goal"), dict) else {}
    plan = state.get("active_action_plan") if isinstance(state.get("active_action_plan"), dict) else {}
    action = state.get("planned_action") if isinstance(state.get("planned_action"), dict) else plan.get("action")
    outcome = state.get("action_outcome") if isinstance(state.get("action_outcome"), dict) else None
    result = state.get("action_result") if isinstance(state.get("action_result"), dict) else {}
    navigation = navigation_from_result(result)
    task = None
    if goal:
        task = {
            "id": goal.get("id"),
            "description": goal.get("description") or goal.get("id"),
            "status": goal.get("status") or "planned",
            "step": int(state.get("step_count", 0)),
            "max_steps": state.get("max_steps"),
            "verification": goal.get("verification"),
        }
    return {
        "updated_at": now_iso(),
        "agent": {
            "phase": phase,
            "objective": state.get("objective"),
            "task": task,
            "action": action,
            "result": outcome or _compact_action_result(result),
            "pipeline": pipeline_for_phase(phase),
            "planner_calls": int(state.get("planner_call_count", 0)),
            "executor_actions": int(state.get("step_count", 0)),
            "interpreter_calls": int(state.get("interpreter_call_count", 0)),
            "plan_error": state.get("plan_error"),
            "interpret_error": state.get("interpret_error"),
            "done": bool(state.get("done", False)),
            "termination_reason": state.get("termination_reason"),
        },
        "navigation": navigation,
        "debug": {
            "state_diff": state.get("state_diff"),
            "action_outcome": outcome,
        },
    }


def navigation_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": result.get("resolved_world_cell") or result.get("requested_world_cell"),
        "path": result.get("planned_world_path") or result.get("planned_path") or [],
    }


def memory_recent(items: dict[str, dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    entries = [
        {
            "key": str(key),
            "value": item.get("value"),
            "source": item.get("source"),
            "updated_at": item.get("updated_at"),
        }
        for key, item in items.items()
        if isinstance(item, dict)
    ]
    entries.sort(key=lambda entry: str(entry.get("updated_at") or ""), reverse=True)
    return entries[:limit]


def state_event_record(event: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    source_type = str(event.get("type") or "state_changed")
    event_type = EVENT_TYPE_BY_STATE_EVENT.get(source_type, "STATE_CHANGED")
    return event_type, _state_event_message(source_type, event), dict(event)


def pipeline_for_phase(phase: str) -> dict[str, str]:
    statuses = {name: "idle" for name in ("planner", "executor", "verifier", "interpreter", "memory")}
    if phase in {"starting", "observed"}:
        statuses["planner"] = "active"
    elif phase == "planned":
        statuses["planner"] = "complete"
        statuses["executor"] = "active"
    elif phase == "executed":
        statuses["planner"] = "complete"
        statuses["executor"] = "complete"
        statuses["verifier"] = "complete"
        statuses["interpreter"] = "active"
    elif phase in {"interpreted", "checkpointed", "completed"}:
        for name in ("planner", "executor", "verifier", "interpreter"):
            statuses[name] = "complete"
        statuses["memory"] = "complete" if phase in {"interpreted", "completed"} else "idle"
    return statuses


def _state_event_message(event_type: str, event: dict[str, Any]) -> str:
    if event_type == "position_changed":
        return f"Position {_format_position(event.get('from'))} -> {_format_position(event.get('to'))}"
    if event_type == "map_changed":
        before = event.get("from") if isinstance(event.get("from"), dict) else {}
        after = event.get("to") if isinstance(event.get("to"), dict) else {}
        return f"Map {before.get('name', 'Unknown')} -> {after.get('name', 'Unknown')}"
    if event_type == "dialog_opened":
        return "Dialog opened"
    if event_type == "dialog_closed":
        return "Dialog closed"
    if event_type == "battle_started":
        return "Battle started"
    if event_type == "battle_ended":
        return "Battle ended"
    if event_type == "item_obtained":
        return f"Obtained {event.get('item', 'item')} x{event.get('quantity', 1)}"
    if event_type == "pokemon_obtained":
        return "Party member obtained"
    return event_type.replace("_", " ").title()


def _party_member(member: dict[str, Any]) -> dict[str, Any]:
    return {
        "species": member.get("species") or "UNKNOWN",
        "species_id": member.get("species_id"),
        "internal_species_id": member.get("internal_species_id"),
        "nickname": member.get("nickname"),
        "level": member.get("level"),
        "hp": member.get("hp"),
        "max_hp": member.get("max_hp"),
        "status": member.get("status"),
        "types": list(member.get("types") or []),
    }


def _item(item: dict[str, Any]) -> dict[str, Any]:
    return {"name": item.get("name") or "UNKNOWN", "quantity": item.get("quantity", 0), "item_id": item.get("item_id")}


def _compact_action_result(result: dict[str, Any]) -> dict[str, Any] | None:
    if not result:
        return None
    return {
        key: value
        for key, value in result.items()
        if key not in {"before_observation", "after_observation"}
    }


def _image_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value.get("base64"):
        return None
    return {
        "format": value.get("format", "png"),
        "width": int(value.get("width", 160)),
        "height": int(value.get("height", 144)),
        "base64": value.get("base64"),
    }


def _position(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict) or value.get("x") is None or value.get("y") is None:
        return None
    return {"x": int(value["x"]), "y": int(value["y"])}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _without_base64(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "base64"}


def _format_position(value: Any) -> str:
    position = _position(value)
    return "unknown" if position is None else f"({position['x']},{position['y']})"

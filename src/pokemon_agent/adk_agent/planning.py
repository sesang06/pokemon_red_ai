from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict

from pokemon_agent.tools.screen_navigation import (
    PLAYER_WALK_CELL,
    compress_collision_to_walk_grid,
    walk_cell_to_screen_tile,
)

PokemonMode = Literal["battle", "dialog", "menu", "overworld", "unknown"]
ALLOWED_BUTTONS = {"a", "b", "start", "select", "left", "right", "up", "down"}


class ActionPlanner(Protocol):
    def plan(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Return one proposed action dict or None."""


class PokemonAgentState(TypedDict, total=False):
    objective: str
    observation: dict[str, Any]
    previous_observation: dict[str, Any]
    mode: PokemonMode
    planned_action: dict[str, Any]
    action_result: dict[str, Any]
    action_history: list[dict[str, Any]]
    stuck_score: int
    step_count: int
    max_steps: int
    checkpoint_every: int
    checkpoint_path: str | None
    plan_error: str | None
    done: bool


def initial_state(
    *,
    objective: str = "safe_loop",
    max_steps: int = 20,
    checkpoint_every: int = 10,
) -> PokemonAgentState:
    return {
        "objective": objective,
        "action_history": [],
        "stuck_score": 0,
        "step_count": 0,
        "max_steps": max_steps,
        "checkpoint_every": checkpoint_every,
        "checkpoint_path": None,
        "plan_error": None,
        "done": False,
    }


def classify_mode(observation: dict[str, Any]) -> PokemonMode:
    state_info = observation.get("state", {})
    mode = state_info.get("mode")
    if state_info.get("in_battle") or mode == "battle":
        return "battle"
    if state_info.get("dialog_open") or mode == "talk":
        return "dialog"
    if mode == "inventory":
        return "menu"
    if mode in {"explore", "navigate", "plan", "start", "building"}:
        return "overworld"
    return "unknown"


def plan_next_action(state: PokemonAgentState, action_planner: ActionPlanner | None = None) -> PokemonAgentState:
    if action_planner is not None:
        try:
            proposed_action = action_planner.plan(dict(state))
            planned_action = sanitize_planned_action(proposed_action)
            if planned_action is not None:
                planned_action.setdefault("source", "adk")
                return {"planned_action": planned_action, "plan_error": None}
        except Exception as exc:
            fallback = rule_based_plan(state)
            fallback["plan_error"] = f"{type(exc).__name__}: {exc}"
            return fallback

    fallback = rule_based_plan(state)
    fallback["plan_error"] = None
    return fallback


def rule_based_plan(state: PokemonAgentState) -> PokemonAgentState:
    mode = state.get("mode", "unknown")
    if mode in {"battle", "dialog"}:
        return {"planned_action": {"type": "press_button", "button": "a", "reason": f"advance_{mode}"}}
    if mode == "menu":
        return {"planned_action": {"type": "press_button", "button": "b", "reason": "close_menu"}}
    if mode != "overworld":
        return {"planned_action": {"type": "step_frames", "frames": 10, "reason": "wait_unknown_mode"}}

    frontier_target = state.get("observation", {}).get("world_map", {}).get("nearest_screen_tile")
    if isinstance(frontier_target, dict):
        return {
            "planned_action": {
                "type": "move_to_screen_tile",
                "target_x": frontier_target["x"],
                "target_y": frontier_target["y"],
                "max_steps": 1,
                "accept_nearest": True,
                "reason": "explore_world_map_frontier",
            }
        }

    target = select_walkable_screen_target(state.get("observation", {}), state.get("step_count", 0))
    if target is None:
        return {"planned_action": {"type": "step_frames", "frames": 10, "reason": "no_walkable_neighbor"}}

    return {
        "planned_action": {
            "type": "move_to_screen_tile",
            "target_x": target["x"],
            "target_y": target["y"],
            "max_steps": 1,
            "accept_nearest": True,
            "reason": "safe_local_explore",
        }
    }


def sanitize_planned_action(action: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None

    action_type = str(action.get("type", ""))
    reason = str(action.get("reason", ""))[:120]
    source = action.get("source")

    try:
        if action_type == "move_to_screen_tile":
            result = {
                "type": "move_to_screen_tile",
                "target_x": bounded_int(action.get("target_x"), minimum=0, maximum=19),
                "target_y": bounded_int(action.get("target_y"), minimum=0, maximum=17),
                "max_steps": bounded_int(action.get("max_steps", 1), minimum=0, maximum=4),
                "accept_nearest": bool(action.get("accept_nearest", True)),
                "reason": reason or "adk_move_to_screen_tile",
            }
        elif action_type == "press_button":
            button = str(action.get("button", ""))
            if button not in ALLOWED_BUTTONS:
                return None
            result = {
                "type": "press_button",
                "button": button,
                "frames": bounded_int(action.get("frames", 4), minimum=1, maximum=30),
                "after_frames": bounded_int(action.get("after_frames", 8), minimum=0, maximum=60),
                "reason": reason or f"adk_press_{button}",
            }
        elif action_type == "execute_actions":
            raw_actions = action.get("actions", [])
            if not isinstance(raw_actions, list):
                return None
            actions: list[dict[str, Any]] = []
            for raw_action in raw_actions[:4]:
                if not isinstance(raw_action, dict):
                    return None
                button = str(raw_action.get("button", ""))
                if button not in ALLOWED_BUTTONS:
                    return None
                actions.append(
                    {
                        "button": button,
                        "frames": bounded_int(raw_action.get("frames", 4), minimum=1, maximum=30),
                        "after_frames": bounded_int(raw_action.get("after_frames", 8), minimum=0, maximum=60),
                    }
                )
            if not actions:
                return None
            result = {
                "type": "execute_actions",
                "actions": actions,
                "reason": reason or "adk_execute_actions",
            }
        elif action_type == "step_frames":
            result = {
                "type": "step_frames",
                "frames": bounded_int(action.get("frames", 10), minimum=1, maximum=30),
                "reason": reason or "adk_step_frames",
            }
        else:
            return None
    except (TypeError, ValueError):
        return None

    if source is not None:
        result["source"] = str(source)[:40]
    return result


def select_walkable_screen_target(observation: dict[str, Any], step_count: int) -> dict[str, int] | None:
    collision = observation.get("game_area_collision")
    if collision is None:
        return None
    walk_grid = compress_collision_to_walk_grid(collision)
    candidates = [
        (1, 0),
        (0, 1),
        (-1, 0),
        (0, -1),
    ]
    offset = step_count % len(candidates)
    for index in range(len(candidates)):
        dx, dy = candidates[(offset + index) % len(candidates)]
        x = PLAYER_WALK_CELL.x + dx
        y = PLAYER_WALK_CELL.y + dy
        if 0 <= y < len(walk_grid) and 0 <= x < len(walk_grid[y]) and walk_grid[y][x]:
            screen_tile = walk_cell_to_screen_tile(type(PLAYER_WALK_CELL)(x, y))
            return {"x": screen_tile.x, "y": screen_tile.y}
    return None


def compact_state_for_prompt(state: dict[str, Any]) -> dict[str, Any]:
    observation = state.get("observation", {})
    game_state = observation.get("state", {})
    return {
        "objective": state.get("objective"),
        "step_count": state.get("step_count", 0),
        "mode": state.get("mode", "unknown"),
        "stuck_score": state.get("stuck_score", 0),
        "state": {
            "map_id": game_state.get("map_id"),
            "map_name": game_state.get("map_name"),
            "position": game_state.get("position"),
            "summary": game_state.get("summary"),
            "in_battle": game_state.get("in_battle"),
            "dialog_open": game_state.get("dialog_open"),
        },
        "player_screen_tile": observation.get("player_screen_tile", {"x": 8, "y": 8}),
        "world_map": observation.get("world_map"),
        "safe_neighbor_screen_tiles": walkable_neighbor_screen_tiles(observation.get("game_area_collision")),
        "recent_actions": state.get("action_history", [])[-5:],
        "instruction": "Choose the next safe action JSON only.",
    }


def walkable_neighbor_screen_tiles(collision: Any) -> list[dict[str, int]]:
    if collision is None:
        return []
    walk_grid = compress_collision_to_walk_grid(collision)
    candidates = [
        ("right", 1, 0),
        ("down", 0, 1),
        ("left", -1, 0),
        ("up", 0, -1),
    ]
    targets: list[dict[str, int]] = []
    for direction, dx, dy in candidates:
        x = PLAYER_WALK_CELL.x + dx
        y = PLAYER_WALK_CELL.y + dy
        if 0 <= y < len(walk_grid) and 0 <= x < len(walk_grid[y]) and walk_grid[y][x]:
            screen_tile = walk_cell_to_screen_tile(type(PLAYER_WALK_CELL)(x, y))
            targets.append({"direction": direction, "x": screen_tile.x, "y": screen_tile.y})
    return targets


def bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
    converted = int(value)
    if converted < minimum or converted > maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return converted

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict

from pokemon_agent.adk_agent.coordinator.action_cycle import normalize_repeat_condition
from pokemon_agent.input_contract import (
    BUTTON_TOKENS,
    MAX_BUTTONS_PER_ACTION,
    MAX_MOVE_PATH_STEPS,
)
from pokemon_agent.tools.pathfinding import GridPoint, reachable_distances
from pokemon_agent.tools.screen_navigation import (
    PLAYER_WALK_CELL,
    walk_cell_to_map_position,
)

PokemonMode = Literal["battle", "dialog", "menu", "overworld", "unknown"]
ALLOWED_BUTTON_TOKENS = frozenset(BUTTON_TOKENS)
PROMPT_MEMORY_LIMIT = 3
PROMPT_TRANSITION_LIMIT = 2
class ActionPlanner(Protocol):
    def plan(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Return one direct action plan dict or None."""


class PokemonAgentState(TypedDict, total=False):
    objective: str
    observation: dict[str, Any]
    previous_observation: dict[str, Any]
    mode: PokemonMode
    long_term_memory: dict[str, Any]
    current_goal: dict[str, Any]
    active_action_plan: dict[str, Any]
    action_outcome: dict[str, Any]
    state_diff: dict[str, Any]
    transition_history: list[dict[str, Any]]
    replan_required: bool
    planner_call_count: int
    llm_planner_call_count: int
    interpreter_call_count: int
    plan_decision: dict[str, Any]
    planned_action: dict[str, Any]
    execution_report: dict[str, Any]
    action_result: dict[str, Any]
    action_history: list[dict[str, Any]]
    session_dialog: list[dict[str, Any]]
    history_summary: str
    memory_path: str
    interpretation: dict[str, Any]
    interpret_error: str | None
    stuck_score: int
    step_count: int
    max_steps: int
    checkpoint_every: int
    checkpoint_path: str | None
    plan_error: str | None
    termination_reason: str | None
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
        "transition_history": [],
        "session_dialog": [],
        "stuck_score": 0,
        "step_count": 0,
        "max_steps": max_steps,
        "checkpoint_every": checkpoint_every,
        "checkpoint_path": None,
        "plan_error": None,
        "replan_required": True,
        "planner_call_count": 0,
        "llm_planner_call_count": 0,
        "interpreter_call_count": 0,
        "termination_reason": None,
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


def rule_based_plan(state: PokemonAgentState) -> PokemonAgentState:
    mode = state.get("mode", "unknown")
    if mode in {"battle", "dialog"}:
        return {"planned_action": {"type": "buttons", "buttons": ["a"], "reason": f"advance_{mode}"}}
    if mode == "menu":
        return {"planned_action": {"type": "buttons", "buttons": ["b"], "reason": "close_menu"}}
    if mode != "overworld":
        return {"planned_action": {"type": "buttons", "buttons": ["wait"], "reason": "wait_unknown_mode"}}

    failed_targets = _failed_move_targets_for_state(state)
    frontier_target = state.get("observation", {}).get("world_map", {}).get("nearest_screen_tile")
    if isinstance(frontier_target, dict):
        world_x = frontier_target.get("world_x")
        world_y = frontier_target.get("world_y")
        distance = int(frontier_target.get("distance") or 0)
        frontier = (int(world_x), int(world_y)) if world_x is not None and world_y is not None else None
        if frontier is not None and distance > 1 and frontier not in failed_targets:
            return {
                "planned_action": {
                    "type": "move",
                    "target": list(frontier),
                    "reason": "explore_world_map_frontier",
                }
            }

    target = select_walkable_world_target(
        state.get("observation", {}),
        state.get("step_count", 0),
        recent_positions=_recent_positions_for_state(state),
        avoid_targets=failed_targets,
    )
    if target is None:
        if isinstance(frontier_target, dict):
            world_x = frontier_target.get("world_x")
            world_y = frontier_target.get("world_y")
            frontier = (int(world_x), int(world_y)) if world_x is not None and world_y is not None else None
            if frontier is not None and frontier not in failed_targets:
                return {
                    "planned_action": {
                        "type": "move",
                        "target": list(frontier),
                        "reason": "explore_adjacent_world_map_frontier",
                    }
                }
        return {"planned_action": {"type": "buttons", "buttons": ["wait"], "reason": "no_walkable_neighbor"}}

    return {
        "planned_action": {
            "type": "move",
            "target": [target["x"], target["y"]],
            "reason": "safe_local_explore",
        }
    }


def _recent_positions_for_state(state: PokemonAgentState) -> list[tuple[int, int]]:
    observation_state = state.get("observation", {}).get("state", {})
    current_map_id = observation_state.get("map_id")
    current_map_name = observation_state.get("map_name")
    positions: list[tuple[int, int]] = []

    def append_position(game_state: Any) -> None:
        if not isinstance(game_state, dict):
            return
        map_id = game_state.get("map_id")
        map_name = game_state.get("map_name")
        if current_map_id is not None and map_id is not None and map_id != current_map_id:
            return
        if current_map_name and map_name and map_name != current_map_name:
            return
        position = game_state.get("position")
        if not isinstance(position, dict) or position.get("x") is None or position.get("y") is None:
            return
        positions.append((int(position["x"]), int(position["y"])))

    for transition in state.get("transition_history", []):
        if isinstance(transition, dict):
            append_position(transition.get("before"))
            append_position(transition.get("after"))
    if not positions:
        for entry in state.get("action_history", []):
            if isinstance(entry, dict):
                append_position(entry.get("before_state"))
                append_position(entry.get("after_state"))
    append_position(observation_state)
    return positions


def _failed_move_targets_for_state(state: PokemonAgentState) -> set[tuple[int, int]]:
    stuck_score = int(state.get("stuck_score", 0))
    if stuck_score <= 0:
        return set()
    failed_reasons = {"no_path", "movement_blocked", "max_steps_reached", "execution_error"}
    failed_targets: set[tuple[int, int]] = set()
    history = list(state.get("action_history", []))[-max(3, stuck_score) :]
    for entry in history:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action") if isinstance(entry.get("action"), dict) else {}
        result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
        target = action.get("target")
        if (
            action.get("type") == "move"
            and result.get("stop_reason") in failed_reasons
            and isinstance(target, (list, tuple))
            and len(target) == 2
        ):
            failed_targets.add((int(target[0]), int(target[1])))
    return failed_targets


def sanitize_planned_action(action: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None

    action_type = str(action.get("type", ""))
    reason = str(action.get("reason", ""))[:120]
    source = action.get("source")

    try:
        if action_type == "move":
            target_x, target_y = _target_from_action(action)
            result = {
                "type": "move",
                "target": [
                    bounded_int(target_x, minimum=0, maximum=255),
                    bounded_int(target_y, minimum=0, maximum=255),
                ],
                "reason": reason or "adk_move",
            }
        elif action_type == "buttons":
            buttons = _buttons_from_action(action)
            if buttons is None:
                return None
            first_button = buttons[0]
            result = {
                "type": "buttons",
                "buttons": buttons,
                "reason": reason or f"adk_buttons_{first_button}",
            }
        else:
            return None
    except (TypeError, ValueError):
        return None

    if source is not None:
        result["source"] = str(source)[:40]
    return result


def normalize_action_plan(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    action = sanitize_planned_action(raw.get("action"))
    if action is None:
        return None

    raw_condition = raw.get("repeat_until")
    repeat_until = normalize_repeat_condition(raw_condition)
    if raw_condition is not None and repeat_until is None:
        return None
    try:
        max_repeats = bounded_int(raw.get("max_repeats", 1), minimum=1, maximum=16)
    except (TypeError, ValueError):
        return None
    if repeat_until is None:
        max_repeats = 1
    return {
        "action": action,
        "repeat_until": repeat_until,
        "max_repeats": max_repeats,
        "repeat_count": 0,
        "status": "active",
    }


def select_walkable_world_target(
    observation: dict[str, Any],
    step_count: int,
    max_distance: int = MAX_MOVE_PATH_STEPS,
    *,
    recent_positions: list[tuple[int, int]] | None = None,
    avoid_targets: set[tuple[int, int]] | None = None,
) -> dict[str, int | str] | None:
    _ = step_count  # Retained for API compatibility; target choice is history-based, not turn rotation.
    walk_grid = walk_area_collision_for_observation(observation)
    position = _position_for_observation(observation)
    if walk_grid is None or position is None:
        return None

    mutable_grid = [list(row) for row in walk_grid]
    if _in_bounds(PLAYER_WALK_CELL, mutable_grid):
        mutable_grid[PLAYER_WALK_CELL.y][PLAYER_WALK_CELL.x] = 1
    distances = reachable_distances(PLAYER_WALK_CELL, mutable_grid)
    if not distances:
        return None

    candidates = [
        point
        for point, distance in distances.items()
        if 0 < distance <= max_distance and point != PLAYER_WALK_CELL
    ]
    if not candidates:
        return None
    cardinal_candidates = [
        point
        for point in candidates
        if point.x == PLAYER_WALK_CELL.x or point.y == PLAYER_WALK_CELL.y
    ]
    if cardinal_candidates:
        candidates = cardinal_candidates

    recent_positions = list(recent_positions or [])
    current_position = (position.x, position.y)
    if not recent_positions or recent_positions[-1] != current_position:
        recent_positions.append(current_position)
    visit_counts: dict[tuple[int, int], int] = {}
    last_seen: dict[tuple[int, int], int] = {}
    for index, visited in enumerate(recent_positions):
        visit_counts[visited] = visit_counts.get(visited, 0) + 1
        last_seen[visited] = index
    direction_counts = {"right": 0, "down": 0, "left": 0, "up": 0}
    for before, after in zip(recent_positions, recent_positions[1:]):
        if before == after:
            continue
        direction = _direction_for_delta(after[0] - before[0], after[1] - before[1])
        direction_counts[direction] += 1
    previous_position = next(
        (visited for visited in reversed(recent_positions[:-1]) if visited != current_position),
        None,
    )

    world_candidates = [(point, walk_cell_to_map_position(point, position)) for point in candidates]
    avoided = set(avoid_targets or set())
    preferred = [candidate for candidate in world_candidates if (candidate[1].x, candidate[1].y) not in avoided]
    if preferred:
        world_candidates = preferred

    direction_priority = {"right": 3, "down": 2, "left": 1, "up": 0}

    def score(candidate: tuple[GridPoint, GridPoint]) -> tuple[int, int, int, int, int, int, int]:
        point, world = candidate
        world_position = (world.x, world.y)
        visits = visit_counts.get(world_position, 0)
        direction = _direction_for_delta(point.x - PLAYER_WALK_CELL.x, point.y - PLAYER_WALK_CELL.y)
        return (
            int(visits == 0),
            int(world_position != previous_position),
            -direction_counts[direction],
            -visits,
            -last_seen.get(world_position, -1),
            distances[point],
            direction_priority[direction],
        )

    target, world = max(world_candidates, key=score)
    direction = _direction_for_delta(target.x - PLAYER_WALK_CELL.x, target.y - PLAYER_WALK_CELL.y)
    return {"x": world.x, "y": world.y, "direction": direction, "distance": distances[target]}


def compact_state_for_prompt(state: dict[str, Any]) -> dict[str, Any]:
    observation = state.get("observation", {})
    if not isinstance(observation, dict):
        observation = {}
    game_state = observation.get("state", {})
    if not isinstance(game_state, dict):
        game_state = {}
    current_goal = state.get("current_goal", {})
    if not isinstance(current_goal, dict):
        current_goal = {}
    active_action_plan = state.get("active_action_plan")
    if not isinstance(active_action_plan, dict):
        active_action_plan = None
    mode = str(state.get("mode") or classify_mode(observation))

    context = {
        "objective": state.get("objective"),
        "current_goal": _compact_goal_for_prompt(current_goal),
        "step_count": state.get("step_count", 0),
        "mode": mode,
        "stuck_score": state.get("stuck_score", 0),
        "state": _canonical_game_state_for_prompt(game_state, mode=mode),
        "last_action_plan": _compact_action_plan_for_prompt(active_action_plan),
        "last_action_outcome": _compact_action_outcome_for_prompt(
            state.get("action_outcome"),
            state.get("state_diff"),
        ),
        "long_term_memory": _compact_memory_for_prompt(
            state.get("long_term_memory"),
            game_state=game_state,
            active_action_plan=active_action_plan,
            objective=str(state.get("objective") or ""),
        ),
        "recent_state_transitions": [
            _compact_transition_for_prompt(entry)
            for entry in state.get("transition_history", [])[-PROMPT_TRANSITION_LIMIT:]
            if isinstance(entry, dict)
        ],
        "last_execution": _compact_execution_for_prompt(state.get("execution_report")),
        "instruction": (
            "Return one direct ActionPlan JSON containing action, optional repeat_until, and max_repeats. "
            "The only action types are buttons and move. For move, copy [x,y] from a navigation.reachable_targets "
            "entry and use its third value as the Dijkstra path length; one call can traverse up to "
            f"{MAX_MOVE_PATH_STEPS} steps. Never create a Task or mark a goal complete; "
            "RAM/structured GameState verification is authoritative."
        ),
    }

    if mode == "overworld":
        context["world_map"] = _world_map_for_prompt(observation.get("world_map"))
        context["navigation"] = _navigation_for_prompt(observation)

    return _without_empty(context)


def _compact_goal_for_prompt(goal: dict[str, Any]) -> dict[str, Any] | None:
    if not goal:
        return None
    verification = goal.get("verification") if isinstance(goal.get("verification"), dict) else {}
    return _without_empty(
        {
            "id": goal.get("id"),
            "description": goal.get("description"),
            "status": goal.get("status"),
            "success_conditions": goal.get("success_conditions"),
            "verified": verification.get("verified"),
        }
    )


def _compact_action_plan_for_prompt(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    return _without_empty(
        {
            "action": _compact_action(plan.get("action")),
            "repeat_until": plan.get("repeat_until"),
            "repeat_count": plan.get("repeat_count"),
            "max_repeats": plan.get("max_repeats"),
            "status": plan.get("status"),
        }
    )


def _compact_action_outcome_for_prompt(result: Any, state_diff: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    diff = state_diff if isinstance(state_diff, dict) else {}
    state_changes = result.get("state_changes") if isinstance(result.get("state_changes"), list) else []
    return _without_empty(
        {
            "status": result.get("status"),
            "action_result": result.get("action_result"),
            "state_changed": bool(state_changes or diff.get("meaningful")),
            "state_changes": state_changes,
            "important_event": result.get("important_event"),
            "goal_completed": result.get("goal_completed"),
            "reason": result.get("reason"),
        }
    )


def _canonical_game_state_for_prompt(game_state: dict[str, Any], *, mode: str) -> dict[str, Any]:
    counts = game_state.get("counts") if isinstance(game_state.get("counts"), dict) else {}
    party = game_state.get("party") if isinstance(game_state.get("party"), list) else []
    items = game_state.get("items") if isinstance(game_state.get("items"), list) else []
    badges = game_state.get("badges") if isinstance(game_state.get("badges"), list) else []
    dialog_open = bool(game_state.get("dialog_open"))
    in_battle = bool(game_state.get("in_battle"))
    menu = game_state.get("menu") if isinstance(game_state.get("menu"), dict) else {}
    menu_open = mode == "menu" or bool(menu.get("active"))
    flags = game_state.get("flags") if isinstance(game_state.get("flags"), dict) else {}

    compact = {
        "map_id": game_state.get("map_id"),
        "map_name": game_state.get("map_name"),
        "position": game_state.get("position"),
        "mode": mode,
        "dialog_open": dialog_open,
        "in_battle": in_battle,
        "menu_open": menu_open,
        "counts": {
            "party": counts.get("party", len(party)),
            "items": counts.get("items", len(items)),
            "badges": counts.get("badges", len(badges)),
        },
        "money": game_state.get("money"),
        "flags": {
            str(key): value
            for key, value in flags.items()
            if isinstance(value, bool) and not str(key).startswith("has_")
        },
    }

    if dialog_open:
        dialog = game_state.get("dialog") if isinstance(game_state.get("dialog"), dict) else {}
        compact["dialog"] = _without_empty(
            {
                "text": dialog.get("text") or game_state.get("dialog_text"),
                "box_detected": dialog.get("box_detected"),
            }
        )
    if in_battle or mode == "battle":
        battle = game_state.get("battle") if isinstance(game_state.get("battle"), dict) else {}
        compact["battle"] = _without_empty(
            {
                "kind": battle.get("kind"),
                "type": battle.get("type"),
                "turns": battle.get("turns"),
                "party": [_compact_party_member(member) for member in party[:6] if isinstance(member, dict)],
            }
        )
    if menu_open:
        compact["menu"] = _without_empty(
            {
                "selection": menu.get("selection"),
                "start_menu_cursor": menu.get("start_menu_cursor"),
            }
        )
    return _without_empty(compact)


def _compact_party_member(member: dict[str, Any]) -> dict[str, Any]:
    return _without_empty(
        {
            "species": member.get("species"),
            "level": member.get("level"),
            "hp": member.get("hp"),
            "max_hp": member.get("max_hp"),
            "status": member.get("status"),
        }
    )


def _compact_memory_for_prompt(
    memory: Any,
    *,
    game_state: dict[str, Any],
    active_action_plan: dict[str, Any] | None,
    objective: str,
) -> dict[str, Any] | None:
    if not isinstance(memory, dict) or not isinstance(memory.get("items"), dict):
        return None
    items = memory["items"]
    plan = active_action_plan or {}
    action = plan.get("action") if isinstance(plan.get("action"), dict) else {}
    map_name = str(game_state.get("map_name") or "")
    action_reason = str(action.get("reason") or action.get("type") or "")
    query_tokens = _prompt_tokens(map_name, objective, action_reason)
    exact_map_key = f"map:{map_name}".lower()

    ranked: list[tuple[int, str, str, Any]] = []
    for raw_key, raw_item in items.items():
        key = str(raw_key)
        item = raw_item if isinstance(raw_item, dict) else {"value": raw_item}
        value = item.get("value")
        searchable = f"{key} {value}".lower()
        score = sum(1 for token in query_tokens if token in searchable)
        if key.lower() == exact_map_key:
            score += 100
        if action_reason and action_reason.lower() in searchable:
            score += 50
        if key.startswith(("failure:", "strategy:")) and score:
            score += 10
        if key == "goal:main" and objective.lower() in searchable:
            score += 5
        ranked.append((score, str(item.get("updated_at") or ""), key, value))

    ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    relevant = [entry for entry in ranked if entry[0] > 0]
    selected = (relevant or ranked)[:PROMPT_MEMORY_LIMIT]
    return {"items": {key: value for _score, _updated_at, key, value in selected}}


def _prompt_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    current: list[str] = []
    for char in " ".join(values).lower():
        if char.isalnum() or char == "_":
            current.append(char)
        elif current:
            token = "".join(current)
            if len(token) >= 3:
                tokens.add(token)
            current = []
    if current:
        token = "".join(current)
        if len(token) >= 3:
            tokens.add(token)
    return tokens


def _compact_transition_for_prompt(entry: dict[str, Any]) -> dict[str, Any]:
    return _without_empty(
        {
            "step": entry.get("step"),
            "action": _compact_action(entry.get("action")),
            "before": _compact_transition_state(entry.get("before")),
            "after": _compact_transition_state(entry.get("after")),
            "state_changes": entry.get("state_changes"),
            "action_status": entry.get("action_status"),
        }
    )


def _compact_execution_for_prompt(report: Any) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    result = report.get("result") if isinstance(report.get("result"), dict) else {}
    return _without_empty(
        {
            "action": _compact_action(report.get("action")),
            "stop_reason": report.get("stop_reason") or result.get("stop_reason"),
            "steps_taken": result.get("steps_taken"),
            "success_hint": report.get("success_hint"),
            "error": result.get("error"),
        }
    )


def _compact_action(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    return _without_empty(
        {
            "type": action.get("type"),
            "target": action.get("target"),
            "buttons": action.get("buttons"),
        }
    )


def _compact_transition_state(state: Any) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    return _without_empty(
        {
            "map_id": state.get("map_id"),
            "map_name": state.get("map_name"),
            "position": state.get("position"),
            "mode": state.get("mode"),
            "dialog_open": state.get("dialog_open"),
            "in_battle": state.get("in_battle"),
        }
    )


def _without_empty(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None and value != [] and value != {}}


def _position_for_observation(observation: dict[str, Any]) -> GridPoint | None:
    position = observation.get("state", {}).get("position")
    if not isinstance(position, dict):
        return None
    return type(PLAYER_WALK_CELL)(int(position["x"]), int(position["y"]))


def _world_map_for_prompt(world_map: Any) -> dict[str, Any] | None:
    if not isinstance(world_map, dict):
        return None
    nearest = world_map.get("nearest_screen_tile")
    nearest_world_cell = None
    if isinstance(nearest, dict) and nearest.get("world_x") is not None and nearest.get("world_y") is not None:
        nearest_world_cell = {
            "x": int(nearest["world_x"]),
            "y": int(nearest["world_y"]),
            "distance": nearest.get("distance"),
        }
    raw_frontier_tiles = world_map.get("frontier_tiles")
    if not isinstance(raw_frontier_tiles, list):
        raw_frontier_tiles = []
    frontier_tiles = [
        _without_empty({"x": tile.get("x"), "y": tile.get("y"), "distance": tile.get("distance")})
        for tile in raw_frontier_tiles[:4]
        if isinstance(tile, dict)
    ]
    return _without_empty(
        {
            "map_id": world_map.get("map_id"),
            "map_name": world_map.get("map_name"),
            "known_tiles": world_map.get("known_tiles"),
            "walkable_tiles": world_map.get("walkable_tiles"),
            "visited_tiles": world_map.get("visited_tiles"),
            "frontier_tiles": frontier_tiles,
            "nearest_frontier_world_cell": nearest_world_cell,
        }
    )


def _navigation_for_prompt(observation: dict[str, Any]) -> dict[str, Any]:
    """Expose exact world-coordinate targets accepted by one bounded move action."""

    navigation: dict[str, Any] = {
        "coordinate_system": "current_map_world",
        "max_path_steps_per_move": MAX_MOVE_PATH_STEPS,
        "reachable_target_format": "[x,y,dijkstra_steps]",
    }
    walk_grid = walk_area_collision_for_observation(observation)
    position = _position_for_observation(observation)
    if walk_grid is None or position is None:
        return navigation

    mutable_grid = [list(row) for row in walk_grid]
    if _in_bounds(PLAYER_WALK_CELL, mutable_grid):
        mutable_grid[PLAYER_WALK_CELL.y][PLAYER_WALK_CELL.x] = 1

    visible_world_cells: list[GridPoint] = []
    for y, row in enumerate(mutable_grid):
        for x in range(len(row)):
            world = walk_cell_to_map_position(GridPoint(x, y), position)
            if 0 <= world.x <= 255 and 0 <= world.y <= 255:
                visible_world_cells.append(world)

    if visible_world_cells:
        navigation["visible_world_bounds"] = {
            "min_x": min(point.x for point in visible_world_cells),
            "max_x": max(point.x for point in visible_world_cells),
            "min_y": min(point.y for point in visible_world_cells),
            "max_y": max(point.y for point in visible_world_cells),
        }
    navigation["player"] = [position.x, position.y]

    distances = reachable_distances(PLAYER_WALK_CELL, mutable_grid)
    reachable_targets: list[list[int]] = []
    for point, distance in distances.items():
        if point == PLAYER_WALK_CELL or not 0 < distance <= MAX_MOVE_PATH_STEPS:
            continue
        world = walk_cell_to_map_position(point, position)
        if 0 <= world.x <= 255 and 0 <= world.y <= 255:
            reachable_targets.append([world.x, world.y, distance])
    reachable_targets.sort(key=lambda target: (target[2], target[1], target[0]))
    navigation["reachable_targets"] = reachable_targets
    return navigation


def _direction_for_delta(dx: int, dy: int) -> str:
    if abs(dx) >= abs(dy) and dx > 0:
        return "right"
    if abs(dx) >= abs(dy) and dx < 0:
        return "left"
    if dy > 0:
        return "down"
    return "up"


def walk_area_collision_for_observation(observation: dict[str, Any]) -> list[list[int]] | None:
    walk_area_collision = observation.get("walk_area_collision")
    if walk_area_collision is not None:
        return _matrix_to_int_rows(walk_area_collision)
    return None


def _matrix_to_int_rows(matrix: Any) -> list[list[int]]:
    rows: list[list[int]] = []
    for row in matrix:
        rows.append([int(value) for value in row])
    return rows


def _in_bounds(point: GridPoint, grid: list[list[int]]) -> bool:
    height = len(grid)
    width = len(grid[0]) if height else 0
    return 0 <= point.x < width and 0 <= point.y < height


def _buttons_from_action(action: dict[str, Any]) -> list[str] | None:
    raw_buttons = action.get("buttons", [])
    if not isinstance(raw_buttons, list) or not 1 <= len(raw_buttons) <= MAX_BUTTONS_PER_ACTION:
        return None

    buttons: list[str] = []
    for raw_button in raw_buttons:
        if not isinstance(raw_button, str):
            return None
        button = str(raw_button or "")
        if button not in ALLOWED_BUTTON_TOKENS:
            return None
        buttons.append(button)
    return buttons or None


def _target_from_action(action: dict[str, Any]) -> tuple[Any, Any]:
    target = action.get("target")
    if not isinstance(target, (list, tuple)) or len(target) != 2:
        raise ValueError("move target must be a two-item list")
    return target[0], target[1]


def bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
    converted = int(value)
    if converted < minimum or converted > maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return converted

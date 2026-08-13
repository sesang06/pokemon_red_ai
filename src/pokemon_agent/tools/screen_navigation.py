from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from pokemon_agent.tools.pathfinding import GridPoint, dijkstra, manhattan, reachable_distances

PLAYER_SCREEN_TILE = GridPoint(8, 8)
PLAYER_WALK_CELL = GridPoint(4, 4)
WALK_CELL_SIZE = 2


@dataclass(frozen=True)
class ScreenPathPlan:
    requested_screen_tile: GridPoint
    requested_walk_cell: GridPoint
    resolved_screen_tile: GridPoint
    resolved_walk_cell: GridPoint
    walk_grid: tuple[tuple[int, ...], ...]
    path: tuple[GridPoint, ...]
    stop_reason: str


def plan_screen_path(
    target_x: int,
    target_y: int,
    collision_matrix: Any,
    *,
    start: GridPoint = PLAYER_WALK_CELL,
    accept_nearest: bool = True,
) -> ScreenPathPlan:
    collision_rows = matrix_to_rows(collision_matrix)
    _validate_target(target_x, target_y, collision_rows)

    walk_grid = compress_collision_to_walk_grid(collision_rows)
    mutable_grid = [list(row) for row in walk_grid]
    if _in_bounds(start, mutable_grid):
        mutable_grid[start.y][start.x] = 1

    requested_walk_cell = screen_tile_to_walk_cell(target_x, target_y)
    resolved_walk_cell = resolve_walk_target(
        start,
        requested_walk_cell,
        mutable_grid,
        accept_nearest=accept_nearest,
    )

    if resolved_walk_cell is None:
        return ScreenPathPlan(
            requested_screen_tile=GridPoint(target_x, target_y),
            requested_walk_cell=requested_walk_cell,
            resolved_screen_tile=walk_cell_to_screen_tile(requested_walk_cell),
            resolved_walk_cell=requested_walk_cell,
            walk_grid=tuple(tuple(row) for row in mutable_grid),
            path=(),
            stop_reason="no_path",
        )

    path = dijkstra(start, resolved_walk_cell, mutable_grid)
    stop_reason = "path_found" if path else "no_path"
    return ScreenPathPlan(
        requested_screen_tile=GridPoint(target_x, target_y),
        requested_walk_cell=requested_walk_cell,
        resolved_screen_tile=walk_cell_to_screen_tile(resolved_walk_cell),
        resolved_walk_cell=resolved_walk_cell,
        walk_grid=tuple(tuple(row) for row in mutable_grid),
        path=tuple(path),
        stop_reason=stop_reason,
    )


def matrix_to_rows(matrix: Any) -> list[list[int]]:
    if hasattr(matrix, "tolist"):
        matrix = matrix.tolist()

    if isinstance(matrix, memoryview):
        matrix = matrix.tolist()

    rows: list[list[int]] = []
    for row in matrix:
        if isinstance(row, Sequence) and not isinstance(row, (bytes, bytearray, str)):
            rows.append([_to_int(value) for value in row])
        else:
            rows.append([_to_int(row)])
    return rows


def compress_collision_to_walk_grid(collision_matrix: Any) -> list[list[int]]:
    rows = matrix_to_rows(collision_matrix)
    height = len(rows)
    width = min((len(row) for row in rows), default=0)
    walk_height = height // WALK_CELL_SIZE
    walk_width = width // WALK_CELL_SIZE

    walk_grid: list[list[int]] = []
    for walk_y in range(walk_height):
        row: list[int] = []
        for walk_x in range(walk_width):
            values = [
                rows[walk_y * WALK_CELL_SIZE + offset_y][walk_x * WALK_CELL_SIZE + offset_x]
                for offset_y in range(WALK_CELL_SIZE)
                for offset_x in range(WALK_CELL_SIZE)
            ]
            row.append(1 if values and all(bool(value) for value in values) else 0)
        walk_grid.append(row)
    return walk_grid


def screen_tile_to_walk_cell(x: int, y: int) -> GridPoint:
    return GridPoint(x // WALK_CELL_SIZE, y // WALK_CELL_SIZE)


def walk_cell_to_screen_tile(point: GridPoint) -> GridPoint:
    return GridPoint(point.x * WALK_CELL_SIZE, point.y * WALK_CELL_SIZE)


def resolve_walk_target(
    start: GridPoint,
    goal: GridPoint,
    walk_grid: Sequence[Sequence[int]],
    *,
    accept_nearest: bool,
) -> GridPoint | None:
    if not _in_bounds(goal, walk_grid):
        return None

    if _is_walkable(goal, walk_grid):
        return goal

    if not accept_nearest:
        return None

    distances = reachable_distances(start, walk_grid)
    if not distances:
        return None

    return min(
        distances,
        key=lambda point: (manhattan(point, goal), distances[point], point.y, point.x),
    )


def grid_point_dict(point: GridPoint) -> dict[str, int]:
    return {"x": point.x, "y": point.y}


def _validate_target(target_x: int, target_y: int, rows: Sequence[Sequence[int]]) -> None:
    height = len(rows)
    width = min((len(row) for row in rows), default=0)
    if width == 0 or height == 0:
        raise ValueError("game_area_collision is empty")
    if not (0 <= target_x < width and 0 <= target_y < height):
        raise ValueError(f"target tile ({target_x}, {target_y}) is outside collision shape {width}x{height}")


def _in_bounds(point: GridPoint, grid: Sequence[Sequence[int]]) -> bool:
    height = len(grid)
    width = len(grid[0]) if height else 0
    return 0 <= point.x < width and 0 <= point.y < height


def _is_walkable(point: GridPoint, grid: Sequence[Sequence[int]]) -> bool:
    return _in_bounds(point, grid) and bool(grid[point.y][point.x])


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0

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


@dataclass(frozen=True)
class WorldPathSegment:
    requested_world_cell: GridPoint
    requested_walk_cell: GridPoint
    segment_walk_cell: GridPoint
    resolved_world_cell: GridPoint
    target_out_of_visible_area: bool
    screen_plan: ScreenPathPlan


def plan_world_path_segment(
    target_x: int,
    target_y: int,
    collision_matrix: Any,
    *,
    player_position: GridPoint,
    start: GridPoint = PLAYER_WALK_CELL,
    accept_nearest: bool = True,
    blocked_edges: set[tuple[GridPoint, GridPoint]] | None = None,
) -> WorldPathSegment:
    """Plan one visible segment toward a current-map world coordinate."""
    requested_world_cell = GridPoint(int(target_x), int(target_y))
    requested_walk_cell = map_position_to_walk_cell(requested_world_cell, player_position)
    target_out_of_visible_area = not walk_cell_in_visible_area(requested_walk_cell)
    segment_walk_cell = (
        clamp_walk_cell_to_visible_area(requested_walk_cell)
        if target_out_of_visible_area
        else requested_walk_cell
    )
    screen_tile = walk_cell_to_screen_tile(segment_walk_cell)
    screen_plan = plan_screen_path(
        screen_tile.x,
        screen_tile.y,
        collision_matrix,
        start=start,
        accept_nearest=accept_nearest,
        blocked_edges=blocked_edges,
    )
    resolved_world_cell = walk_cell_to_map_position(screen_plan.resolved_walk_cell, player_position)
    return WorldPathSegment(
        requested_world_cell=requested_world_cell,
        requested_walk_cell=requested_walk_cell,
        segment_walk_cell=segment_walk_cell,
        resolved_world_cell=resolved_world_cell,
        target_out_of_visible_area=target_out_of_visible_area,
        screen_plan=screen_plan,
    )


def plan_screen_path(
    target_x: int,
    target_y: int,
    collision_matrix: Any,
    *,
    start: GridPoint = PLAYER_WALK_CELL,
    accept_nearest: bool = True,
    blocked_edges: set[tuple[GridPoint, GridPoint]] | None = None,
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
        blocked_edges=blocked_edges,
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

    path = dijkstra(start, resolved_walk_cell, mutable_grid, blocked_edges=blocked_edges)
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


def screen_tile_to_map_position(
    tile_x: int,
    tile_y: int,
    player_position: GridPoint,
    *,
    player_walk_cell: GridPoint = PLAYER_WALK_CELL,
) -> GridPoint:
    walk_cell = screen_tile_to_walk_cell(tile_x, tile_y)
    return walk_cell_to_map_position(walk_cell, player_position, player_walk_cell=player_walk_cell)


def walk_cell_to_map_position(
    walk_cell: GridPoint,
    player_position: GridPoint,
    *,
    player_walk_cell: GridPoint = PLAYER_WALK_CELL,
) -> GridPoint:
    return GridPoint(
        player_position.x + walk_cell.x - player_walk_cell.x,
        player_position.y + walk_cell.y - player_walk_cell.y,
    )


def map_position_to_walk_cell(
    map_position: GridPoint,
    player_position: GridPoint,
    *,
    player_walk_cell: GridPoint = PLAYER_WALK_CELL,
) -> GridPoint:
    return GridPoint(
        player_walk_cell.x + map_position.x - player_position.x,
        player_walk_cell.y + map_position.y - player_position.y,
    )


def walk_cell_in_visible_area(point: GridPoint) -> bool:
    return 0 <= point.x <= 9 and 0 <= point.y <= 8


def clamp_walk_cell_to_visible_area(point: GridPoint) -> GridPoint:
    return GridPoint(
        max(0, min(9, int(point.x))),
        max(0, min(8, int(point.y))),
    )


def resolve_walk_target(
    start: GridPoint,
    goal: GridPoint,
    walk_grid: Sequence[Sequence[int]],
    *,
    accept_nearest: bool,
    blocked_edges: set[tuple[GridPoint, GridPoint]] | None = None,
) -> GridPoint | None:
    if not _in_bounds(goal, walk_grid):
        return None

    if _is_walkable(goal, walk_grid) and dijkstra(start, goal, walk_grid, blocked_edges=blocked_edges):
        return goal

    if not accept_nearest:
        return None

    distances = reachable_distances(start, walk_grid, blocked_edges=blocked_edges)
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

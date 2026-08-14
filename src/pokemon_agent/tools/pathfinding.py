from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Sequence


@dataclass(frozen=True, order=True)
class GridPoint:
    x: int
    y: int


def astar(
    start: GridPoint,
    goal: GridPoint,
    walkable_grid: Sequence[Sequence[int]],
) -> list[GridPoint]:
    width = len(walkable_grid[0]) if walkable_grid else 0
    height = len(walkable_grid)
    if width == 0 or height == 0:
        return []

    def in_bounds(point: GridPoint) -> bool:
        return 0 <= point.x < width and 0 <= point.y < height

    def is_walkable(point: GridPoint) -> bool:
        return bool(walkable_grid[point.y][point.x])

    if not in_bounds(start) or not in_bounds(goal):
        return []

    if not is_walkable(start) or not is_walkable(goal):
        return []

    frontier: list[tuple[int, int, GridPoint]] = []
    heappush(frontier, (0, 0, start))
    came_from: dict[GridPoint, GridPoint | None] = {start: None}
    cost_so_far: dict[GridPoint, int] = {start: 0}
    counter = 0

    while frontier:
        _, _, current = heappop(frontier)
        if current == goal:
            break

        for neighbor in neighbors(current, width, height):
            if not is_walkable(neighbor):
                continue
            new_cost = cost_so_far[current] + 1
            if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                cost_so_far[neighbor] = new_cost
                counter += 1
                priority = new_cost + manhattan(neighbor, goal)
                heappush(frontier, (priority, counter, neighbor))
                came_from[neighbor] = current

    if goal not in came_from:
        return []

    return reconstruct_path(came_from, goal)


def dijkstra(
    start: GridPoint,
    goal: GridPoint,
    walkable_grid: Sequence[Sequence[int]],
    *,
    blocked_edges: set[tuple[GridPoint, GridPoint]] | None = None,
) -> list[GridPoint]:
    width = len(walkable_grid[0]) if walkable_grid else 0
    height = len(walkable_grid)
    if width == 0 or height == 0:
        return []

    def in_bounds(point: GridPoint) -> bool:
        return 0 <= point.x < width and 0 <= point.y < height

    def is_walkable(point: GridPoint) -> bool:
        return bool(walkable_grid[point.y][point.x])

    if not in_bounds(start) or not in_bounds(goal):
        return []

    if not is_walkable(start) or not is_walkable(goal):
        return []

    frontier: list[tuple[int, int, GridPoint]] = []
    heappush(frontier, (0, 0, start))
    came_from: dict[GridPoint, GridPoint | None] = {start: None}
    cost_so_far: dict[GridPoint, int] = {start: 0}
    counter = 0

    while frontier:
        _, _, current = heappop(frontier)
        if current == goal:
            break

        for neighbor in neighbors(current, width, height):
            if not is_walkable(neighbor):
                continue
            if blocked_edges is not None and (current, neighbor) in blocked_edges:
                continue
            new_cost = cost_so_far[current] + 1
            if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                cost_so_far[neighbor] = new_cost
                counter += 1
                heappush(frontier, (new_cost, counter, neighbor))
                came_from[neighbor] = current

    if goal not in came_from:
        return []

    return reconstruct_path(came_from, goal)


def reachable_distances(
    start: GridPoint,
    walkable_grid: Sequence[Sequence[int]],
    *,
    blocked_edges: set[tuple[GridPoint, GridPoint]] | None = None,
) -> dict[GridPoint, int]:
    width = len(walkable_grid[0]) if walkable_grid else 0
    height = len(walkable_grid)
    if width == 0 or height == 0:
        return {}
    if not (0 <= start.x < width and 0 <= start.y < height):
        return {}
    if not walkable_grid[start.y][start.x]:
        return {}

    frontier: list[tuple[int, int, GridPoint]] = []
    heappush(frontier, (0, 0, start))
    distances: dict[GridPoint, int] = {start: 0}
    counter = 0

    while frontier:
        current_cost, _, current = heappop(frontier)
        if current_cost > distances[current]:
            continue

        for neighbor in neighbors(current, width, height):
            if not walkable_grid[neighbor.y][neighbor.x]:
                continue
            if blocked_edges is not None and (current, neighbor) in blocked_edges:
                continue
            new_cost = current_cost + 1
            if neighbor not in distances or new_cost < distances[neighbor]:
                distances[neighbor] = new_cost
                counter += 1
                heappush(frontier, (new_cost, counter, neighbor))

    return distances


def neighbors(point: GridPoint, width: int, height: int) -> list[GridPoint]:
    candidates = [
        GridPoint(point.x + 1, point.y),
        GridPoint(point.x - 1, point.y),
        GridPoint(point.x, point.y + 1),
        GridPoint(point.x, point.y - 1),
    ]
    return [candidate for candidate in candidates if 0 <= candidate.x < width and 0 <= candidate.y < height]


def manhattan(left: GridPoint, right: GridPoint) -> int:
    return abs(left.x - right.x) + abs(left.y - right.y)


def reconstruct_path(came_from: dict[GridPoint, GridPoint | None], goal: GridPoint) -> list[GridPoint]:
    path = [goal]
    current = goal
    while came_from[current] is not None:
        current = came_from[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return path


def directions_from_path(path: Sequence[GridPoint]) -> list[str]:
    directions: list[str] = []
    for previous, current in zip(path, path[1:]):
        dx = current.x - previous.x
        dy = current.y - previous.y
        if dx == 1:
            directions.append("right")
        elif dx == -1:
            directions.append("left")
        elif dy == 1:
            directions.append("down")
        elif dy == -1:
            directions.append("up")
        else:
            raise ValueError(f"Non-adjacent path segment: {previous} -> {current}")
    return directions

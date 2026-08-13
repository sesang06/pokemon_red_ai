from pokemon_agent.tools.pathfinding import GridPoint, dijkstra, directions_from_path
from pokemon_agent.tools.screen_navigation import (
    compress_collision_to_walk_grid,
    plan_screen_path,
    resolve_walk_target,
)


def test_collision_is_compressed_to_2_by_2_walk_cells() -> None:
    collision = [
        [1, 1, 1, 1],
        [1, 1, 1, 0],
        [1, 1, 0, 0],
        [1, 1, 0, 0],
    ]

    assert compress_collision_to_walk_grid(collision) == [[1, 0], [1, 0]]


def test_dijkstra_routes_around_wall() -> None:
    grid = [
        [1, 1, 1, 1],
        [1, 0, 0, 1],
        [1, 1, 1, 1],
    ]

    path = dijkstra(GridPoint(0, 0), GridPoint(3, 2), grid)

    assert path[0] == GridPoint(0, 0)
    assert path[-1] == GridPoint(3, 2)
    assert directions_from_path(path) == ["right", "right", "right", "down", "down"]


def test_blocked_target_resolves_to_nearest_reachable() -> None:
    grid = [
        [1, 1, 1],
        [1, 0, 0],
        [1, 1, 1],
    ]

    resolved = resolve_walk_target(GridPoint(0, 0), GridPoint(1, 1), grid, accept_nearest=True)

    assert resolved in {GridPoint(1, 0), GridPoint(0, 1), GridPoint(2, 2)}


def test_plan_screen_path_returns_no_path_when_nearest_disabled() -> None:
    collision = [[1 for _ in range(20)] for _ in range(18)]
    collision[8][10] = 0
    collision[8][11] = 0
    collision[9][10] = 0
    collision[9][11] = 0

    plan = plan_screen_path(10, 8, collision, accept_nearest=False)

    assert plan.stop_reason == "no_path"
    assert plan.path == ()

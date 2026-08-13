from pokemon_agent.tools.pathfinding import GridPoint, astar, directions_from_path


def test_astar_routes_around_wall() -> None:
    grid = [
        [1, 1, 1, 1],
        [1, 0, 0, 1],
        [1, 1, 1, 1],
    ]

    path = astar(GridPoint(0, 0), GridPoint(3, 2), grid)

    assert path[0] == GridPoint(0, 0)
    assert path[-1] == GridPoint(3, 2)
    assert all(grid[point.y][point.x] for point in path)
    assert directions_from_path(path) == ["right", "right", "right", "down", "down"]


def test_astar_returns_empty_when_goal_blocked() -> None:
    grid = [
        [1, 1],
        [1, 0],
    ]

    assert astar(GridPoint(0, 0), GridPoint(1, 1), grid) == []

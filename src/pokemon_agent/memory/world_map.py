from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pokemon_agent.tools.screen_navigation import (
    PLAYER_WALK_CELL,
    compress_collision_to_walk_grid,
    walk_cell_to_screen_tile,
)


@dataclass
class WorldTile:
    walkable: bool
    visits: int = 0


@dataclass
class WorldMap:
    map_id: int
    map_name: str
    tiles: dict[tuple[int, int], WorldTile] = field(default_factory=dict)
    player_position: tuple[int, int] | None = None

    def mark_tile(self, x: int, y: int, walkable: bool) -> None:
        existing = self.tiles.get((x, y))
        if existing is None:
            self.tiles[(x, y)] = WorldTile(walkable=walkable)
            return
        existing.walkable = existing.walkable or walkable

    def mark_visit(self, x: int, y: int) -> None:
        tile = self.tiles.setdefault((x, y), WorldTile(walkable=True))
        tile.walkable = True
        tile.visits += 1
        self.player_position = (x, y)

    @property
    def known_count(self) -> int:
        return len(self.tiles)

    @property
    def walkable_count(self) -> int:
        return sum(1 for tile in self.tiles.values() if tile.walkable)

    @property
    def visited_count(self) -> int:
        return sum(1 for tile in self.tiles.values() if tile.visits > 0)

    def frontier_tiles(self, limit: int = 12) -> list[dict[str, int]]:
        if self.player_position is None:
            return []

        frontier: list[tuple[int, int, int]] = []
        for (x, y), tile in self.tiles.items():
            if not tile.walkable or tile.visits > 0:
                continue
            distance = abs(x - self.player_position[0]) + abs(y - self.player_position[1])
            frontier.append((distance, x, y))

        frontier.sort()
        return [{"x": x, "y": y, "distance": distance} for distance, x, y in frontier[:limit]]

    def nearest_frontier_screen_tile(self) -> dict[str, int] | None:
        if self.player_position is None:
            return None

        for frontier in self.frontier_tiles(limit=64):
            dx = frontier["x"] - self.player_position[0]
            dy = frontier["y"] - self.player_position[1]
            if abs(dx) > PLAYER_WALK_CELL.x or abs(dy) > PLAYER_WALK_CELL.y:
                continue
            walk_cell_x = PLAYER_WALK_CELL.x + dx
            walk_cell_y = PLAYER_WALK_CELL.y + dy
            if not (0 <= walk_cell_x <= 9 and 0 <= walk_cell_y <= 8):
                continue
            screen_tile = walk_cell_to_screen_tile(type(PLAYER_WALK_CELL)(walk_cell_x, walk_cell_y))
            return {
                "x": screen_tile.x,
                "y": screen_tile.y,
                "world_x": frontier["x"],
                "world_y": frontier["y"],
                "distance": frontier["distance"],
            }
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "map_name": self.map_name,
            "player_position": None
            if self.player_position is None
            else {"x": self.player_position[0], "y": self.player_position[1]},
            "known_tiles": self.known_count,
            "walkable_tiles": self.walkable_count,
            "visited_tiles": self.visited_count,
            "frontier_tiles": self.frontier_tiles(),
            "nearest_screen_tile": self.nearest_frontier_screen_tile(),
        }

    def render_ascii(self, radius: int = 12) -> str:
        if self.player_position is None:
            return f"{self.map_name} ({self.map_id})\n(no player position)"

        px, py = self.player_position
        lines = [
            f"{self.map_name} ({self.map_id})",
            f"known={self.known_count} walkable={self.walkable_count} visited={self.visited_count}",
            f"player=({px},{py})",
            "",
        ]
        for y in range(py - radius, py + radius + 1):
            chars: list[str] = []
            for x in range(px - radius, px + radius + 1):
                if (x, y) == (px, py):
                    chars.append("@")
                    continue
                tile = self.tiles.get((x, y))
                if tile is None:
                    chars.append(" ")
                elif tile.visits > 0:
                    chars.append("o")
                elif tile.walkable:
                    chars.append(".")
                else:
                    chars.append("#")
            lines.append("".join(chars))
        return "\n".join(lines)


class WorldMapTracker:
    def __init__(self):
        self.maps: dict[int, WorldMap] = {}
        self.current_map_id: int | None = None

    def update_from_observation(self, observation: dict[str, Any]) -> dict[str, Any] | None:
        state = observation.get("state", {})
        map_id = state.get("map_id")
        position = state.get("position")
        collision = observation.get("game_area_collision")
        if map_id is None or position is None or collision is None:
            return None

        world_map = self.maps.setdefault(
            int(map_id),
            WorldMap(map_id=int(map_id), map_name=str(state.get("map_name", f"Map {map_id}"))),
        )
        world_map.map_name = str(state.get("map_name", world_map.map_name))
        px = int(position["x"])
        py = int(position["y"])
        world_map.mark_visit(px, py)
        self.current_map_id = world_map.map_id

        walk_grid = compress_collision_to_walk_grid(collision)
        for walk_y, row in enumerate(walk_grid):
            for walk_x, walkable in enumerate(row):
                world_x = px + walk_x - PLAYER_WALK_CELL.x
                world_y = py + walk_y - PLAYER_WALK_CELL.y
                world_map.mark_tile(world_x, world_y, bool(walkable))

        return world_map.summary()

    def current_map(self) -> WorldMap | None:
        if self.current_map_id is None:
            return None
        return self.maps.get(self.current_map_id)

    def current_summary(self) -> dict[str, Any] | None:
        world_map = self.current_map()
        return None if world_map is None else world_map.summary()

    def current_ascii(self, radius: int = 12) -> str:
        world_map = self.current_map()
        if world_map is None:
            return "World map not available yet."
        return world_map.render_ascii(radius=radius)

    def nearest_frontier_screen_tile(self) -> dict[str, int] | None:
        world_map = self.current_map()
        if world_map is None:
            return None
        return world_map.nearest_frontier_screen_tile()

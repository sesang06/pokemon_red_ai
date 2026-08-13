from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WalkabilityGrid:
    cells: tuple[tuple[int, ...], ...]

    @classmethod
    def from_matrix(cls, matrix: Any) -> "WalkabilityGrid":
        return cls(tuple(tuple(int(value) for value in row) for row in matrix))


class TileDetector:
    """Use PyBoy's game wrapper when available, with simple fallbacks later."""

    def walkability_from_game_wrapper(self, game_wrapper: Any) -> WalkabilityGrid | None:
        if not hasattr(game_wrapper, "game_area_collision"):
            return None
        return WalkabilityGrid.from_matrix(game_wrapper.game_area_collision())

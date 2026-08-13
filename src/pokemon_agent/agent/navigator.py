from __future__ import annotations

from collections.abc import Sequence

from pokemon_agent.agent.actions import ButtonAction, Decision
from pokemon_agent.memory.world_state import GameState, Position
from pokemon_agent.tools.pathfinding import GridPoint, astar, directions_from_path


class NavigationAgent:
    def decide(
        self,
        state: GameState,
        target: Position,
        walkable_grid: Sequence[Sequence[int]] | None = None,
    ) -> Decision:
        if state.position is None:
            return Decision.wait(10, "navigation_waiting_for_position")

        if state.position == target:
            return Decision.wait(1, "navigation_target_reached")

        if walkable_grid is None:
            return self._greedy_step(state.position, target)

        start = GridPoint(state.position.x, state.position.y)
        goal = GridPoint(target.x, target.y)
        path = astar(start, goal, walkable_grid)
        if not path or len(path) < 2:
            return Decision.wait(15, "navigation_no_path")

        direction = directions_from_path(path)[0]
        return Decision(
            reason=f"navigation_step_{direction}",
            actions=(ButtonAction(direction, frames=4, after_frames=12),),
            settle_frames=1,
        )

    def _greedy_step(self, position: Position, target: Position) -> Decision:
        dx = target.x - position.x
        dy = target.y - position.y

        if abs(dx) >= abs(dy) and dx != 0:
            button = "right" if dx > 0 else "left"
        elif dy != 0:
            button = "down" if dy > 0 else "up"
        else:
            return Decision.wait(1, "navigation_target_reached")

        return Decision(
            reason=f"navigation_greedy_{button}",
            actions=(ButtonAction(button, frames=4, after_frames=12),),
            settle_frames=1,
        )

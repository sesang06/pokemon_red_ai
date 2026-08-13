from __future__ import annotations

from pokemon_agent.agent.actions import Decision
from pokemon_agent.memory.world_state import GameState


class InventoryAgent:
    """Placeholder for menu navigation and item-use policies."""

    def decide(self, state: GameState) -> Decision:
        if not state.items:
            return Decision.wait(1, "inventory_no_items_known")

        return Decision.wait(1, "inventory_policy_not_configured")

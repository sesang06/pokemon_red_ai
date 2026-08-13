from __future__ import annotations

from pokemon_agent.agent.actions import ButtonAction, Decision
from pokemon_agent.memory.world_state import GameState


class DialogAgent:
    def decide(self, state: GameState) -> Decision:
        if not state.dialog_open:
            return Decision.wait(1, "dialog_not_active")

        return Decision(
            reason="dialog_advance_text",
            actions=(ButtonAction("a", frames=2, after_frames=12),),
            settle_frames=1,
        )

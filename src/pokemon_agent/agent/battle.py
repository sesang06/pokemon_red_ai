from __future__ import annotations

from pokemon_agent.agent.actions import ButtonAction, Decision
from pokemon_agent.memory.world_state import GameState


class RuleBasedBattleAgent:
    """Starter battle policy.

    This is deliberately simple: it keeps selecting the default fight option.
    Detailed battle parsing can replace this without touching navigation.
    """

    def decide(self, state: GameState) -> Decision:
        if not state.in_battle:
            return Decision.wait(1, "battle_not_active")

        return Decision(
            reason="battle_default_confirm",
            actions=(ButtonAction("a", frames=2, after_frames=18),),
            settle_frames=1,
        )

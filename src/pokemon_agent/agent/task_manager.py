from __future__ import annotations

from dataclasses import dataclass

from pokemon_agent.agent.actions import Decision
from pokemon_agent.agent.battle import RuleBasedBattleAgent
from pokemon_agent.agent.dialog import DialogAgent
from pokemon_agent.agent.inventory import InventoryAgent
from pokemon_agent.agent.navigator import NavigationAgent
from pokemon_agent.agent.planner import Planner
from pokemon_agent.memory.world_state import GameMode, GameState


@dataclass
class TaskManager:
    planner: Planner
    navigator: NavigationAgent
    battle: RuleBasedBattleAgent
    dialog: DialogAgent
    inventory: InventoryAgent

    def decide(self, state: GameState) -> Decision:
        if state.in_battle:
            return self.battle.decide(state)

        if state.dialog_open:
            return self.dialog.decide(state)

        if state.mode == GameMode.INVENTORY:
            return self.inventory.decide(state)

        goal = self.planner.next_goal(state)
        if goal.target_position is not None:
            return self.navigator.decide(state, goal.target_position)

        return Decision.wait(30, f"planner_goal_{goal.name}")

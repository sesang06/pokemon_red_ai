from pokemon_agent.agent.battle import RuleBasedBattleAgent
from pokemon_agent.agent.dialog import DialogAgent
from pokemon_agent.agent.inventory import InventoryAgent
from pokemon_agent.agent.navigator import NavigationAgent
from pokemon_agent.agent.planner import ScriptedPlanner
from pokemon_agent.agent.task_manager import TaskManager
from pokemon_agent.memory.world_state import GameMode, GameState, ItemStack


def make_manager() -> TaskManager:
    return TaskManager(
        planner=ScriptedPlanner(),
        navigator=NavigationAgent(),
        battle=RuleBasedBattleAgent(),
        dialog=DialogAgent(),
        inventory=InventoryAgent(),
    )


def test_task_manager_routes_battle_first() -> None:
    decision = make_manager().decide(GameState(in_battle=True, dialog_open=True))

    assert decision.reason == "battle_default_confirm"


def test_task_manager_routes_dialog_when_not_battle() -> None:
    decision = make_manager().decide(GameState(dialog_open=True))

    assert decision.reason == "dialog_advance_text"


def test_task_manager_routes_inventory_mode() -> None:
    decision = make_manager().decide(
        GameState(mode=GameMode.INVENTORY, items=[ItemStack(name="Potion", quantity=1)])
    )

    assert decision.reason == "inventory_policy_not_configured"

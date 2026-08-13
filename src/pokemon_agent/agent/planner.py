from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pokemon_agent.memory.world_state import GameState, Position


@dataclass(frozen=True)
class Goal:
    name: str
    target_map: str | None = None
    target_position: Position | None = None
    description: str = ""


class Planner(Protocol):
    def next_goal(self, state: GameState) -> Goal:
        """Return the next high-level objective."""


class ScriptedPlanner:
    """Small deterministic planner used until an LLM planner is wired in."""

    def next_goal(self, state: GameState) -> Goal:
        if state.map_name == "Pallet Town":
            return Goal(
                name="leave_pallet_town",
                target_map="Pallet Town",
                target_position=Position(x=10, y=1),
                description="Reach the north exit toward Route 1.",
            )

        if state.map_name == "Viridian City":
            return Goal(
                name="visit_mart",
                target_map="Viridian City",
                description="Find the mart and obtain Oak's Parcel.",
            )

        return Goal(
            name="explore",
            target_map=state.map_name,
            description="Explore safely and update memory.",
        )

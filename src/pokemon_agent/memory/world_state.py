from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GameMode(StrEnum):
    START = "start"
    PLAN = "plan"
    EXPLORE = "explore"
    NAVIGATE = "navigate"
    BATTLE = "battle"
    TALK = "talk"
    BUILDING = "building"
    INVENTORY = "inventory"


@dataclass(frozen=True)
class Position:
    x: int
    y: int


@dataclass(frozen=True)
class PartyMember:
    species: str
    level: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    species_id: int | None = None
    internal_species_id: int | None = None
    nickname: str | None = None
    status: str | None = None
    types: list[str] = field(default_factory=list)
    moves: list[str] = field(default_factory=list)
    move_pp: list[int] = field(default_factory=list)
    trainer_id: int | None = None
    experience: int | None = None


@dataclass(frozen=True)
class ItemStack:
    name: str
    quantity: int
    item_id: int | None = None


@dataclass(frozen=True)
class NpcObservation:
    label: str
    distance: int | None = None
    position: Position | None = None


@dataclass(frozen=True)
class ExitObservation:
    direction: str
    target_map: str | None = None


@dataclass
class GameState:
    map_id: int | None = None
    map_name: str = "Unknown"
    position: Position | None = None
    facing: str | None = None
    mode: GameMode = GameMode.START
    in_battle: bool = False
    dialog_open: bool = False
    player_name: str | None = None
    rival_name: str | None = None
    money: int | None = None
    coins: int | None = None
    game_time: str | None = None
    tileset: str | None = None
    pokedex_caught: int | None = None
    badges: list[str] = field(default_factory=list)
    party: list[PartyMember] = field(default_factory=list)
    items: list[ItemStack] = field(default_factory=list)
    warps: list[Position] = field(default_factory=list)
    dialog_text: str | None = None
    nearby_npcs: list[NpcObservation] = field(default_factory=list)
    nearby_exits: list[ExitObservation] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        pos = "unknown" if self.position is None else f"({self.position.x},{self.position.y})"
        return (
            f"map={self.map_name} map_id={self.map_id} pos={pos} "
            f"mode={self.mode} battle={self.in_battle} dialog={self.dialog_open}"
        )

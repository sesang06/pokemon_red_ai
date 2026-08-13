from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pokemon_agent.memory.world_state import GameMode, GameState, Position


class MemoryView(Protocol):
    def __getitem__(self, address: int) -> int:
        """Read a byte from emulator memory."""


POKEMON_RED_MAP_NAMES: dict[int, str] = {
    0x00: "Pallet Town",
    0x01: "Viridian City",
    0x02: "Pewter City",
    0x03: "Cerulean City",
    0x04: "Lavender Town",
    0x05: "Vermilion City",
    0x06: "Celadon City",
    0x07: "Fuchsia City",
    0x08: "Cinnabar Island",
    0x09: "Indigo Plateau",
    0x0C: "Route 1",
    0x0D: "Route 2",
}


@dataclass(frozen=True)
class PokemonRedRamMap:
    current_map: int = 0xD35E
    player_y: int = 0xD361
    player_x: int = 0xD362
    collision_ptr_lo: int = 0xD530
    collision_ptr_hi: int = 0xD531
    grass_tile: int = 0xD535
    tileset_type: int = 0xFFD7


class PokemonRedMemoryReader:
    def __init__(self, ram_map: PokemonRedRamMap | None = None):
        self.ram_map = ram_map or PokemonRedRamMap()

    def read(self, memory: MemoryView) -> GameState:
        raw = self.read_raw(memory)
        map_id = raw["current_map"]
        position = Position(x=raw["player_x"], y=raw["player_y"])

        return GameState(
            map_id=map_id,
            map_name=POKEMON_RED_MAP_NAMES.get(map_id, f"Map {map_id:#04x}"),
            position=position,
            mode=GameMode.EXPLORE,
            raw=raw,
        )

    def read_raw(self, memory: MemoryView) -> dict[str, int]:
        addresses = self.ram_map
        collision_ptr = self._read_u16(memory, addresses.collision_ptr_lo)
        return {
            "current_map": self._read_u8(memory, addresses.current_map),
            "player_y": self._read_u8(memory, addresses.player_y),
            "player_x": self._read_u8(memory, addresses.player_x),
            "collision_ptr": collision_ptr,
            "grass_tile": self._read_u8(memory, addresses.grass_tile),
            "tileset_type": self._read_u8(memory, addresses.tileset_type),
        }

    @staticmethod
    def _read_u8(memory: MemoryView, address: int) -> int:
        return int(memory[address]) & 0xFF

    @classmethod
    def _read_u16(cls, memory: MemoryView, address: int) -> int:
        return cls._read_u8(memory, address) | (cls._read_u8(memory, address + 1) << 8)

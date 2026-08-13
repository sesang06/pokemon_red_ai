from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from pokemon_agent.memory.memory_reader import POKEMON_RED_MAP_NAMES


class MemoryView(Protocol):
    def __getitem__(self, address: int) -> int:
        """Read a byte from emulator memory, as in pyboy.memory[0xD35E]."""


Formatter = Callable[[MemoryView, int, int], str]


@dataclass(frozen=True)
class RamWatchField:
    address: int
    label: str
    width: int = 1
    formatter: Formatter | None = None


@dataclass(frozen=True)
class RamWatchSection:
    title: str
    fields: tuple[RamWatchField, ...]


def format_ram_watch(memory: MemoryView) -> str:
    """Return a compact interpreted RAM-map view for the UI.

    Address meanings are based on Data Crystal's Pokemon Red/Blue RAM map:
    https://datacrystal.tcrf.net/wiki/Pok%C3%A9mon_Red_and_Blue/RAM_map
    """

    lines = [
        "Data Crystal RAM map view",
        "read style: pyboy.memory[0xADDR]",
        "",
    ]

    for section in RAM_WATCH_SECTIONS:
        lines.append(f"[{section.title}]")
        for field in section.fields:
            lines.append(_format_field(memory, field))
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_field(memory: MemoryView, field: RamWatchField) -> str:
    raw = _raw_bytes(memory, field.address, field.width)
    formatted = field.formatter(memory, field.address, field.width) if field.formatter else _decimal(memory, field.address, field.width)
    raw_text = " ".join(f"{value:02X}" for value in raw)
    return f"pyboy.memory[0x{field.address:04X}] {raw_text:<8} -> {formatted:<18} # {field.label}"


def _read_u8(memory: MemoryView, address: int) -> int:
    return int(memory[address]) & 0xFF


def _raw_bytes(memory: MemoryView, address: int, width: int) -> list[int]:
    return [_read_u8(memory, address + offset) for offset in range(width)]


def _decimal(memory: MemoryView, address: int, width: int) -> str:
    if width == 1:
        return str(_read_u8(memory, address))
    return str(_read_u16_be(memory, address))


def _hex_byte(memory: MemoryView, address: int, _width: int) -> str:
    return f"0x{_read_u8(memory, address):02X}"


def _u16_be(memory: MemoryView, address: int, _width: int) -> str:
    return str(_read_u16_be(memory, address))


def _u16_le_hex(memory: MemoryView, address: int, _width: int) -> str:
    return f"0x{_read_u16_le(memory, address):04X}"


def _read_u16_be(memory: MemoryView, address: int) -> int:
    return (_read_u8(memory, address) << 8) | _read_u8(memory, address + 1)


def _read_u16_le(memory: MemoryView, address: int) -> int:
    return _read_u8(memory, address) | (_read_u8(memory, address + 1) << 8)


def _map_id(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"{value} ({POKEMON_RED_MAP_NAMES.get(value, f'Map {value:#04x}')})"


def _badges(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"{value.bit_count()}/8 bits={value:08b}"


def _bcd_money(memory: MemoryView, address: int, width: int) -> str:
    digits: list[str] = []
    for value in _raw_bytes(memory, address, width):
        for nibble in ((value >> 4) & 0x0F, value & 0x0F):
            if nibble > 9:
                return f"invalid BCD raw={value:02X}"
            digits.append(str(nibble))
    return str(int("".join(digits)))


def _critical_flag(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    if value == 0x01:
        meaning = "Critical Hit"
    elif value == 0x02:
        meaning = "One-hit KO"
    elif value == 0:
        meaning = "None"
    else:
        meaning = "Unknown"
    return f"0x{value:02X} ({meaning})"


def _options(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    animation = "off" if value & 0x80 else "on"
    battle_style = "set" if value & 0x40 else "shift"
    text_speed = value & 0x0F
    return f"0x{value:02X} anim={animation} style={battle_style} text={text_speed}"


RAM_WATCH_SECTIONS: tuple[RamWatchSection, ...] = (
    RamWatchSection(
        "Player / Map",
        (
            RamWatchField(0xD35E, "Current Map Number", formatter=_map_id),
            RamWatchField(0xD361, "Current Player Y-Position"),
            RamWatchField(0xD362, "Current Player X-Position"),
            RamWatchField(0xD363, "Current Player Y-Position (Block)"),
            RamWatchField(0xD364, "Current Player X-Position (Block)"),
            RamWatchField(0xD365, "Last map location for certain exits", formatter=_map_id),
            RamWatchField(0xD356, "Badges (bit switches)", formatter=_badges),
            RamWatchField(0xD347, "Money", width=3, formatter=_bcd_money),
        ),
    ),
    RamWatchSection(
        "Party",
        (
            RamWatchField(0xD163, "# Pokemon In Party"),
            RamWatchField(0xD164, "Party Pokemon 1 ID"),
            RamWatchField(0xD165, "Party Pokemon 2 ID"),
            RamWatchField(0xD166, "Party Pokemon 3 ID"),
            RamWatchField(0xD167, "Party Pokemon 4 ID"),
            RamWatchField(0xD168, "Party Pokemon 5 ID"),
            RamWatchField(0xD169, "Party Pokemon 6 ID"),
            RamWatchField(0xD16C, "Pokemon 1 Current HP", width=2, formatter=_u16_be),
            RamWatchField(0xD18C, "Pokemon 1 Level (actual)"),
            RamWatchField(0xD18D, "Pokemon 1 Max HP", width=2, formatter=_u16_be),
        ),
    ),
    RamWatchSection(
        "Items",
        (
            RamWatchField(0xD31D, "Total Items"),
            RamWatchField(0xD31E, "Item 1 ID"),
            RamWatchField(0xD31F, "Item 1 Quantity"),
            RamWatchField(0xD320, "Item 2 ID"),
            RamWatchField(0xD321, "Item 2 Quantity"),
            RamWatchField(0xD322, "Item 3 ID"),
            RamWatchField(0xD323, "Item 3 Quantity"),
            RamWatchField(0xD324, "Item 4 ID"),
            RamWatchField(0xD325, "Item 4 Quantity"),
            RamWatchField(0xD326, "Item 5 ID"),
            RamWatchField(0xD327, "Item 5 Quantity"),
        ),
    ),
    RamWatchSection(
        "Map Header / Tileset",
        (
            RamWatchField(0xD367, "Map's Tileset"),
            RamWatchField(0xD368, "Map's Height (Blocks)"),
            RamWatchField(0xD369, "Map's Width (Blocks)"),
            RamWatchField(0xD36A, "Map's Data pointer", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD36C, "Map's Text Pointer Table", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD36E, "Map's Level-Script Pointer", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD370, "Map's Connection Byte", formatter=_hex_byte),
            RamWatchField(0xD530, "Pointer to Collision Data", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD535, "Grass Tile", formatter=_hex_byte),
        ),
    ),
    RamWatchSection(
        "Battle / Menu",
        (
            RamWatchField(0xCC26, "Currently selected menu item"),
            RamWatchField(0xCC2D, "Last cursor position on START / battle menu"),
            RamWatchField(0xCCD5, "Number of turns in current battle"),
            RamWatchField(0xD057, "Type of battle"),
            RamWatchField(0xD05A, "Battle Type"),
            RamWatchField(0xD05E, "Critical Hit / OHKO Flag", formatter=_critical_flag),
            RamWatchField(0xD0D8, "Damage attack is about to do", formatter=_hex_byte),
        ),
    ),
    RamWatchSection(
        "Options / Audio",
        (
            RamWatchField(0xD355, "Options", formatter=_options),
            RamWatchField(0xD358, "Text delay override bits", formatter=_hex_byte),
            RamWatchField(0xD359, "Player ID", width=2, formatter=_u16_be),
            RamWatchField(0xD35B, "Audio track in current map", formatter=_hex_byte),
            RamWatchField(0xD35C, "Audio bank in current map", formatter=_hex_byte),
            RamWatchField(0xD35D, "Map palette control", formatter=_hex_byte),
        ),
    ),
)

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from pokemon_agent.memory.memory_reader import (
    ITEM_NAMES,
    MOVE_NAMES,
    PLAYER_FACING_DIRECTIONS,
    POKEMON_RED_MAP_NAMES,
    POKEMON_SPECIES_NAMES,
    POKEMON_TYPE_NAMES,
    TEXT_OVERRIDES,
    TILESET_NAMES,
)
from pokemon_agent.memory.world_state import GameState, Position


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


def format_ram_watch(memory: MemoryView, state: GameState | None = None) -> str:
    """Return a compact interpreted RAM-map view for the UI.

    Address meanings are based on Data Crystal's Pokemon Red/Blue RAM map:
    https://datacrystal.tcrf.net/wiki/Pok%C3%A9mon_Red_and_Blue/RAM_map
    """

    lines = [
        "Data Crystal RAM map view",
        "read style: pyboy.memory[0xADDR]",
        "",
    ]

    if state is not None:
        lines.extend(_format_interpreted_state(state))
        lines.append("")

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


def _format_interpreted_state(state: GameState) -> list[str]:
    lines = [
        "[Interpreted GameState]",
        f"Summary: {state.summary()}",
        f"Player: {_optional_text(state.player_name)}",
        f"Rival: {_optional_text(state.rival_name)}",
        f"Money: {_optional_value(state.money)}",
        f"Coins: {_optional_value(state.coins)}",
        f"Game time: {_optional_text(state.game_time)}",
        f"Tileset: {_optional_text(state.tileset)}",
        f"Pokedex caught: {_optional_value(state.pokedex_caught)}",
        f"Badges: {_format_list(state.badges)}",
        f"Warps: {_format_positions(state.warps)}",
        f"Dialog open: {state.dialog_open}",
        "Dialog text:",
    ]
    lines.extend(_indent_block(state.dialog_text or "(none decoded)"))
    lines.append("Party:")
    if state.party:
        for index, member in enumerate(state.party, start=1):
            moves = ", ".join(
                f"{move} PP={pp}" for move, pp in zip(member.moves, member.move_pp, strict=False)
            )
            if not moves:
                moves = _format_list(member.moves)
            lines.append(
                "  "
                f"{index}. {member.species}"
                f" pokedex_id={_optional_value(member.species_id)}"
                f" internal_id={_optional_value(member.internal_species_id)}"
                f" nick={_optional_text(member.nickname)}"
                f" level={_optional_value(member.level)}"
                f" hp={_optional_value(member.hp)}/{_optional_value(member.max_hp)}"
                f" status={_optional_text(member.status)}"
                f" types={_format_list(member.types)}"
                f" moves={moves}"
                f" trainer_id={_optional_value(member.trainer_id)}"
                f" exp={_optional_value(member.experience)}"
            )
    else:
        lines.append("  (none)")

    lines.append("Items:")
    if state.items:
        for index, item in enumerate(state.items, start=1):
            lines.append(f"  {index}. {item.name} x{item.quantity} id={_optional_value(item.item_id)}")
    else:
        lines.append("  (none)")

    raw = state.raw
    if raw:
        lines.append("Raw derived fields:")
        for key in sorted(raw):
            lines.append(f"  {key}: {raw[key]}")

    return lines


def _indent_block(text: str) -> list[str]:
    return [f"  {line}" for line in str(text).splitlines() or [""]]


def _optional_text(value: str | None) -> str:
    return value if value else "(none)"


def _optional_value(value: object) -> str:
    return "(unknown)" if value is None else str(value)


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "(none)"


def _format_positions(positions: list[Position]) -> str:
    return ", ".join(f"({position.x},{position.y})" for position in positions) if positions else "(none)"


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


def _facing_direction(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"0x{value:02X} ({PLAYER_FACING_DIRECTIONS.get(value, 'unknown')})"


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


def _pokemon_text(memory: MemoryView, address: int, width: int) -> str:
    result = ""
    for value in _raw_bytes(memory, address, width):
        if value == 0x00:
            continue
        if value == 0x50:
            break
        if value == 0x7F:
            result += " "
        elif 0x80 <= value <= 0x99:
            result += chr(value - 0x80 + ord("A"))
        elif 0xA0 <= value <= 0xB9:
            result += chr(value - 0xA0 + ord("a"))
        elif 0xF6 <= value <= 0xFF:
            result += str(value - 0xF6)
        elif value in TEXT_OVERRIDES:
            result += TEXT_OVERRIDES[value]
        else:
            result += f"[{value:02X}]"
    return " ".join(result.strip().split()) or "(empty)"


def _tileset(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"{value} ({TILESET_NAMES.get(value, f'Tileset 0x{value:02X}')})"


def _species(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"0x{value:02X} ({POKEMON_SPECIES_NAMES.get(value, f'UNKNOWN_{value:02X}')})"


def _move(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"0x{value:02X} ({MOVE_NAMES.get(value, f'UNKNOWN_{value:02X}')})"


def _type(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    return f"0x{value:02X} ({POKEMON_TYPE_NAMES.get(value, f'TYPE_{value:02X}')})"


def _item(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    if 0xC4 <= value <= 0xC8:
        name = f"HM{value - 0xC3:02d}"
    elif 0xC9 <= value <= 0xFE:
        name = f"TM{value - 0xC8:02d}"
    else:
        name = ITEM_NAMES.get(value, f"UNKNOWN_{value:02X}")
    return f"0x{value:02X} ({name})"


def _status(memory: MemoryView, address: int, _width: int) -> str:
    value = _read_u8(memory, address)
    if value & 0b111:
        name = "Sleep"
    elif value & 0b0100_0000:
        name = "Paralysis"
    elif value & 0b0010_0000:
        name = "Freeze"
    elif value & 0b0001_0000:
        name = "Burn"
    elif value & 0b0000_1000:
        name = "Poison"
    else:
        name = "OK"
    return f"0x{value:02X} ({name})"


def _game_time(memory: MemoryView, address: int, _width: int) -> str:
    hours = _read_u16_be(memory, address)
    minutes = _read_u8(memory, address + 2)
    seconds = _read_u8(memory, address + 4)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


RAM_WATCH_SECTIONS: tuple[RamWatchSection, ...] = (
    RamWatchSection(
        "Player / Map",
        (
            RamWatchField(0xC109, "Player facing direction", formatter=_facing_direction),
            RamWatchField(0xD35E, "Current Map Number", formatter=_map_id),
            RamWatchField(0xD361, "Current Player Y-Position"),
            RamWatchField(0xD362, "Current Player X-Position"),
            RamWatchField(0xD363, "Current Player Y-Position (Block)"),
            RamWatchField(0xD364, "Current Player X-Position (Block)"),
            RamWatchField(0xD365, "Last map location for certain exits", formatter=_map_id),
            RamWatchField(0xD356, "Badges (bit switches)", formatter=_badges),
            RamWatchField(0xD347, "Money", width=3, formatter=_bcd_money),
            RamWatchField(0xD158, "Player name", width=11, formatter=_pokemon_text),
            RamWatchField(0xD34A, "Rival name", width=11, formatter=_pokemon_text),
            RamWatchField(0xD5A4, "Game Corner coins", width=2, formatter=_u16_be),
            RamWatchField(0xDA40, "Game time hhhh:mm:ss", width=5, formatter=_game_time),
        ),
    ),
    RamWatchSection(
        "Party",
        (
            RamWatchField(0xD163, "# Pokemon In Party"),
            RamWatchField(0xD164, "Party Pokemon 1 ID", formatter=_species),
            RamWatchField(0xD165, "Party Pokemon 2 ID", formatter=_species),
            RamWatchField(0xD166, "Party Pokemon 3 ID", formatter=_species),
            RamWatchField(0xD167, "Party Pokemon 4 ID", formatter=_species),
            RamWatchField(0xD168, "Party Pokemon 5 ID", formatter=_species),
            RamWatchField(0xD169, "Party Pokemon 6 ID", formatter=_species),
            RamWatchField(0xD16B, "Pokemon 1 Species", formatter=_species),
            RamWatchField(0xD16C, "Pokemon 1 Current HP", width=2, formatter=_u16_be),
            RamWatchField(0xD16F, "Pokemon 1 Status", formatter=_status),
            RamWatchField(0xD170, "Pokemon 1 Type 1", formatter=_type),
            RamWatchField(0xD171, "Pokemon 1 Type 2", formatter=_type),
            RamWatchField(0xD173, "Pokemon 1 Move 1", formatter=_move),
            RamWatchField(0xD174, "Pokemon 1 Move 2", formatter=_move),
            RamWatchField(0xD175, "Pokemon 1 Move 3", formatter=_move),
            RamWatchField(0xD176, "Pokemon 1 Move 4", formatter=_move),
            RamWatchField(0xD188, "Pokemon 1 Move 1 PP"),
            RamWatchField(0xD189, "Pokemon 1 Move 2 PP"),
            RamWatchField(0xD18A, "Pokemon 1 Move 3 PP"),
            RamWatchField(0xD18B, "Pokemon 1 Move 4 PP"),
            RamWatchField(0xD18C, "Pokemon 1 Level (actual)"),
            RamWatchField(0xD18D, "Pokemon 1 Max HP", width=2, formatter=_u16_be),
            RamWatchField(0xD2B5, "Pokemon 1 Nickname", width=11, formatter=_pokemon_text),
        ),
    ),
    RamWatchSection(
        "Items",
        (
            RamWatchField(0xD31D, "Total Items"),
            RamWatchField(0xD31E, "Item 1 ID", formatter=_item),
            RamWatchField(0xD31F, "Item 1 Quantity"),
            RamWatchField(0xD320, "Item 2 ID", formatter=_item),
            RamWatchField(0xD321, "Item 2 Quantity"),
            RamWatchField(0xD322, "Item 3 ID", formatter=_item),
            RamWatchField(0xD323, "Item 3 Quantity"),
            RamWatchField(0xD324, "Item 4 ID", formatter=_item),
            RamWatchField(0xD325, "Item 4 Quantity"),
            RamWatchField(0xD326, "Item 5 ID", formatter=_item),
            RamWatchField(0xD327, "Item 5 Quantity"),
        ),
    ),
    RamWatchSection(
        "Map Header / Tileset",
        (
            RamWatchField(0xD367, "Map's Tileset", formatter=_tileset),
            RamWatchField(0xD368, "Map's Height (Blocks)"),
            RamWatchField(0xD369, "Map's Width (Blocks)"),
            RamWatchField(0xD36A, "Map's Data pointer", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD36C, "Map's Text Pointer Table", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD36E, "Map's Level-Script Pointer", width=2, formatter=_u16_le_hex),
            RamWatchField(0xD370, "Map's Connection Byte", formatter=_hex_byte),
            RamWatchField(0xD3AE, "Number of warps"),
            RamWatchField(0xD3AF, "Warp 1 row"),
            RamWatchField(0xD3B0, "Warp 1 col"),
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

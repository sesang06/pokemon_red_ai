from pokemon_agent.memory.ram_map import format_ram_watch
from pokemon_agent.memory.world_state import GameMode, GameState, ItemStack, PartyMember, Position


class FakeMemory(dict[int, int]):
    def __getitem__(self, address: int) -> int:
        return dict.get(self, address, 0)


def test_format_ram_watch_uses_pyboy_memory_style() -> None:
    memory = FakeMemory(
        {
            0xD35E: 0x00,
            0xD361: 6,
            0xD362: 5,
            0xD356: 0b00000101,
            0xD347: 0x01,
            0xD348: 0x23,
            0xD349: 0x45,
        }
    )

    text = format_ram_watch(memory)

    assert "pyboy.memory[0xD35E]" in text
    assert "Current Map Number" in text
    assert "Pallet Town" in text
    assert "12345" in text
    assert "2/8" in text


def test_format_ram_watch_includes_interpreted_dialog_and_details() -> None:
    state = GameState(
        map_id=0x28,
        map_name="Oak's Lab",
        position=Position(7, 8),
        mode=GameMode.TALK,
        dialog_open=True,
        player_name="RED",
        rival_name="BLUE",
        money=12345,
        coins=500,
        game_time="1:02:03",
        tileset="Pokemon Center",
        pokedex_caught=3,
        badges=["Boulder", "Thunder"],
        warps=[Position(3, 2)],
        dialog_text="HI\nTHERE",
        party=[
            PartyMember(
                species="Bulbasaur",
                level=5,
                hp=35,
                max_hp=45,
                species_id=0x99,
                nickname="BULB",
                status="Poison",
                types=["Grass", "Poison"],
                moves=["Tackle", "Growl"],
                move_pp=[35, 40],
                trainer_id=0x1234,
                experience=0x123,
            )
        ],
        items=[ItemStack("Potion", 3, item_id=0x14)],
        raw={"collision_ptr": 0x1234},
    )

    text = format_ram_watch(FakeMemory(), state)

    assert "[Interpreted GameState]" in text
    assert "Dialog open: True" in text
    assert "Dialog text:" in text
    assert "HI" in text
    assert "THERE" in text
    assert "Bulbasaur" in text
    assert "Potion x3" in text
    assert "collision_ptr: 4660" in text

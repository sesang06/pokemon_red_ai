from pokemon_agent.memory.memory_reader import PokemonRedMemoryReader, PokemonRedRamMap


class FakeMemory(dict[int, int]):
    def __getitem__(self, address: int) -> int:
        return dict.get(self, address, 0)


def test_memory_reader_builds_base_world_state() -> None:
    ram = PokemonRedRamMap()
    memory = FakeMemory(
        {
            ram.current_map: 0x00,
            ram.player_x: 5,
            ram.player_y: 6,
            ram.collision_ptr_lo: 0x34,
            ram.collision_ptr_hi: 0x12,
            ram.grass_tile: 0x52,
            ram.tileset_type: 1,
        }
    )

    state = PokemonRedMemoryReader(ram).read(memory)

    assert state.map_name == "Pallet Town"
    assert state.position is not None
    assert state.position.x == 5
    assert state.position.y == 6
    assert state.raw["collision_ptr"] == 0x1234

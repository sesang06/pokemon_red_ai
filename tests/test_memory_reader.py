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


def test_memory_reader_decodes_player_facing_direction() -> None:
    ram = PokemonRedRamMap()
    reader = PokemonRedMemoryReader(ram)

    for raw_value, expected in ((0x00, "down"), (0x04, "up"), (0x08, "left"), (0x0C, "right")):
        state = reader.read(FakeMemory({ram.player_facing: raw_value}))
        assert state.facing == expected
        assert state.raw["player_facing"] == raw_value


def test_memory_reader_only_decodes_opponent_during_battle() -> None:
    ram = PokemonRedRamMap()
    reader = PokemonRedMemoryReader(ram)
    enemy_memory = {
        ram.enemy_species: 0xA5,
        ram.enemy_hp: 0x00,
        ram.enemy_hp + 1: 9,
        ram.enemy_status: 0x08,
        ram.enemy_type_1: 0x00,
        ram.enemy_type_2: 0x00,
        ram.enemy_level: 4,
        ram.enemy_max_hp: 0x00,
        ram.enemy_max_hp + 1: 15,
    }

    assert reader.read(FakeMemory(enemy_memory)).battle_opponent is None

    opponent = reader.read(FakeMemory({**enemy_memory, ram.battle_type: 1})).battle_opponent
    assert opponent is not None
    assert opponent.species == "Rattata"
    assert opponent.level == 4
    assert opponent.hp == 9
    assert opponent.max_hp == 15
    assert opponent.status == "Poison"
    assert opponent.types == ["Normal"]
    assert not hasattr(opponent, "moves")
    assert not hasattr(opponent, "move_pp")


def test_memory_reader_builds_detailed_world_state() -> None:
    memory = FakeMemory()
    memory.update(
        {
            0xD35E: 0x28,
            0xD362: 7,
            0xD361: 8,
            0xD367: 0x06,
            0xD347: 0x01,
            0xD348: 0x23,
            0xD349: 0x45,
            0xD356: 0b00000101,
            0xD359: 0x12,
            0xD35A: 0x34,
            0xD163: 1,
            0xD164: 0x99,
            0xD31D: 2,
            0xD31E: 0x14,
            0xD31F: 3,
            0xD320: 0xC4,
            0xD321: 1,
            0xD5A4: 0x01,
            0xD5A5: 0xF4,
            0xDA40: 0,
            0xDA41: 1,
            0xDA42: 2,
            0xDA44: 3,
            0xD3AE: 1,
            0xD3AF: 2,
            0xD3B0: 3,
            0xD2F7: 0b00001011,
            0xC3A0: 0x7C,
            0xC3A1: 0x87,
            0xC3A2: 0x88,
            0xC3A3: 0x7C,
        }
    )
    _write_text(memory, 0xD158, "RED")
    _write_text(memory, 0xD34A, "BLUE")
    _write_party_slot(memory, 0xD16B)
    _write_text(memory, 0xD2B5, "BULB")

    state = PokemonRedMemoryReader().read(memory)

    assert state.map_name == "Oak's Lab"
    assert state.player_name == "RED"
    assert state.rival_name == "BLUE"
    assert state.money == 12345
    assert state.coins == 500
    assert state.game_time == "1:02:03"
    assert state.tileset == "Pokemon Center"
    assert state.badges == ["Boulder", "Thunder"]
    assert state.pokedex_caught == 3
    assert state.dialog_open is True
    assert state.dialog_text == "HI"
    assert state.warps[0].x == 3
    assert state.warps[0].y == 2
    assert state.items[0].name == "Potion"
    assert state.items[1].name == "HM01"

    bulbasaur = state.party[0]
    assert bulbasaur.species == "Bulbasaur"
    assert bulbasaur.species_id == 1
    assert bulbasaur.internal_species_id == 0x99
    assert bulbasaur.nickname == "BULB"
    assert bulbasaur.hp == 35
    assert bulbasaur.max_hp == 45
    assert bulbasaur.level == 5
    assert bulbasaur.status == "Poison"
    assert bulbasaur.types == ["Grass", "Poison"]
    assert bulbasaur.moves == ["Tackle", "Growl"]
    assert bulbasaur.move_pp == [35, 40]
    assert bulbasaur.trainer_id == 0x1234
    assert bulbasaur.experience == 0x0123


def test_party_species_ids_are_national_pokedex_numbers() -> None:
    expected = ((0x99, 1, "Bulbasaur"), (0xB0, 4, "Charmander"), (0xB1, 7, "Squirtle"))
    for internal_id, pokedex_id, species in expected:
        memory = FakeMemory({0xD163: 1, 0xD164: internal_id, 0xD16B: internal_id})
        member = PokemonRedMemoryReader().read_party_pokemon(memory)[0]
        assert member.species == species
        assert member.species_id == pokedex_id
        assert member.internal_species_id == internal_id


def test_memory_reader_exposes_transient_input_lock_state() -> None:
    ram = PokemonRedRamMap()
    memory = FakeMemory({ram.joy_ignore: 0xF0, ram.status_flags_5: 0x01})

    state = PokemonRedMemoryReader(ram).read(memory)

    assert state.raw["joy_ignore"] == 0xF0
    assert state.raw["status_flags_5"] == 0x01
    assert state.raw["controls_locked"] is True


def test_memory_reader_decodes_end_dialog_tile() -> None:
    ram = PokemonRedRamMap()
    memory = FakeMemory(
        {
            ram.tilemap_start: 0x7C,
            ram.tilemap_start + 1: 0xF0,
            ram.tilemap_start + 2: 0x7C,
        }
    )

    state = PokemonRedMemoryReader(ram).read(memory)

    assert state.dialog_open is True
    assert state.dialog_text == "END"


def _write_party_slot(memory: FakeMemory, base: int) -> None:
    memory.update(
        {
            base: 0x99,
            base + 1: 0,
            base + 2: 35,
            base + 4: 0x08,
            base + 5: 0x16,
            base + 6: 0x03,
            base + 8: 0x21,
            base + 9: 0x2D,
            base + 12: 0x12,
            base + 13: 0x34,
            base + 0x1A: 0,
            base + 0x1B: 0x01,
            base + 0x1C: 0x23,
            base + 0x1D: 35,
            base + 0x1E: 40,
            base + 0x21: 5,
            base + 0x22: 0,
            base + 0x23: 45,
        }
    )


def _write_text(memory: FakeMemory, address: int, text: str) -> None:
    for offset, char in enumerate(text):
        memory[address + offset] = ord(char) - ord("A") + 0x80
    memory[address + len(text)] = 0x50

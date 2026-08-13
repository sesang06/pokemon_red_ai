from pokemon_agent.memory.ram_map import format_ram_watch


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

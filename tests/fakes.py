from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


class FakeMemory:
    def __init__(self, values: dict[int, int] | None = None):
        self.values = values or {}

    def __getitem__(self, address: int) -> int:
        return self.values.get(address, 0)

    def __setitem__(self, address: int, value: int) -> None:
        self.values[address] = value & 0xFF


class FakePokemonEnvironment:
    def __init__(self):
        self.memory = FakeMemory(
            {
                0xD35E: 0,
                0xD361: 6,
                0xD362: 5,
                0xD530: 0,
                0xD531: 0,
                0xD535: 0,
                0xFFD7: 0,
            }
        )
        self.buttons: list[tuple[str, int]] = []
        self.ticks: list[tuple[int, bool]] = []
        self.saved_paths: list[Path] = []
        self.loaded_paths: list[Path] = []
        self.stopped = False
        self.emulation_speed: int | None = None
        self.collision = [[1 for _ in range(20)] for _ in range(18)]
        self.area = [[x + y * 20 for x in range(20)] for y in range(18)]

    def button(self, button: str, frames: int = 1) -> None:
        self.buttons.append((button, frames))
        if button == "right":
            self.memory[0xD362] = self.memory[0xD362] + 1
        elif button == "left":
            self.memory[0xD362] = self.memory[0xD362] - 1
        elif button == "down":
            self.memory[0xD361] = self.memory[0xD361] + 1
        elif button == "up":
            self.memory[0xD361] = self.memory[0xD361] - 1

    def tick(self, frames: int = 1, render: bool = False) -> bool:
        self.ticks.append((frames, render))
        return True

    def set_emulation_speed(self, speed: int) -> None:
        self.emulation_speed = int(speed)

    def screen_image(self) -> Image.Image:
        return Image.new("RGB", (160, 144), "red")

    def game_area(self) -> list[list[int]]:
        return self.area

    def game_area_collision(self) -> list[list[int]]:
        return self.collision

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-state")
        self.saved_paths.append(path)

    def load_state(self, path: Path) -> None:
        self.loaded_paths.append(path)

    def stop(self, save: bool = False) -> None:
        self.stopped = True


def fake_session_paths(tmp_path: Path) -> Any:
    from pokemon_agent.session import PokemonSessionPaths

    rom = tmp_path / "src" / "pokered.gb"
    rom.parent.mkdir(parents=True, exist_ok=True)
    rom.write_bytes(b"fake-rom")
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    fixed_state = state_dir / "fixed_start.state"
    fixed_state.write_bytes(b"fake-state")
    return PokemonSessionPaths(
        project_root=tmp_path,
        rom=rom,
        state_dir=state_dir,
        fixed_state=fixed_state,
        last_state=state_dir / "last.state",
    )

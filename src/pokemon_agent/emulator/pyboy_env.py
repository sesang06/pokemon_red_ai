from __future__ import annotations

from pathlib import Path
from typing import Any


class PyBoyEnvironment:
    """Thin wrapper around a live PyBoy object."""

    def __init__(self, rom_path: Path, window: str = "null", symbols: Path | None = None):
        try:
            from pyboy import PyBoy
        except ImportError as exc:
            raise RuntimeError("Install pyboy to run the emulator layer.") from exc

        kwargs: dict[str, Any] = {"window": window}
        if symbols is not None:
            kwargs["symbols"] = str(symbols)

        self.pyboy = PyBoy(str(rom_path), **kwargs)

    @property
    def memory(self) -> Any:
        return self.pyboy.memory

    @property
    def game_wrapper(self) -> Any:
        return self.pyboy.game_wrapper

    def button(self, button: str, frames: int = 1) -> None:
        self.pyboy.button(button, frames)

    def tick(self, frames: int = 1, render: bool = False) -> bool:
        return bool(self.pyboy.tick(frames, render))

    def screen_ndarray(self) -> Any:
        return self.pyboy.screen.ndarray

    def screen_image(self) -> Any:
        return self.pyboy.screen.image

    def background_tilemap(self) -> Any:
        return self.pyboy.tilemap_background[:, :]

    def window_tilemap(self) -> Any:
        return self.pyboy.tilemap_window[:, :]

    def game_area(self) -> Any:
        return self.pyboy.game_area()

    def game_area_collision(self) -> Any:
        return self.pyboy.game_area_collision()

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            self.pyboy.save_state(handle)

    def load_state(self, path: Path) -> None:
        with path.open("rb") as handle:
            self.pyboy.load_state(handle)

    def stop(self, save: bool = False) -> None:
        self.pyboy.stop(save=save)

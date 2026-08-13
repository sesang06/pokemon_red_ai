from __future__ import annotations

from typing import Any, Protocol


class ScreenEnvironment(Protocol):
    def screen_ndarray(self) -> Any:
        """Return the PyBoy screen ndarray."""

    def background_tilemap(self) -> Any:
        """Return the background tile map."""

    def window_tilemap(self) -> Any:
        """Return the window tile map."""


class ScreenReader:
    def __init__(self, env: ScreenEnvironment):
        self.env = env

    def rgb_frame(self) -> Any:
        frame = self.env.screen_ndarray()
        return frame[:, :, :3].copy()

    def background_tiles(self) -> Any:
        return self.env.background_tilemap()

    def window_tiles(self) -> Any:
        return self.env.window_tilemap()

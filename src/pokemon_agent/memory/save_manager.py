from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SaveStateEnvironment(Protocol):
    def save_state(self, path: Path) -> None:
        """Persist emulator state."""

    def load_state(self, path: Path) -> None:
        """Restore emulator state."""


@dataclass
class SaveStateManager:
    env: SaveStateEnvironment
    directory: Path

    def checkpoint(self, name: str) -> Path:
        path = self.directory / f"{name}.state"
        self.env.save_state(path)
        return path

    def restore(self, name: str) -> Path:
        path = self.directory / f"{name}.state"
        self.env.load_state(path)
        return path

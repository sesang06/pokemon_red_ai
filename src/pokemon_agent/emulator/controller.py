from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pokemon_agent.agent.actions import ButtonAction


class ButtonEnvironment(Protocol):
    def button(self, button: str, frames: int = 1) -> None:
        """Press a Game Boy button."""

    def tick(self, frames: int = 1, render: bool = False) -> bool:
        """Advance the emulator."""


class ActionExecutor:
    def __init__(self, env: ButtonEnvironment):
        self.env = env

    def execute(self, actions: Iterable[ButtonAction]) -> None:
        for action in actions:
            self.env.button(action.button, frames=action.frames)
            self.env.tick(max(action.frames, 1), render=False)
            if action.after_frames > 0:
                self.env.tick(action.after_frames, render=False)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Button = Literal["a", "b", "start", "select", "left", "right", "up", "down"]


@dataclass(frozen=True)
class ButtonAction:
    button: Button
    frames: int = 1
    after_frames: int = 8


@dataclass(frozen=True)
class Decision:
    reason: str
    actions: tuple[ButtonAction, ...] = field(default_factory=tuple)
    settle_frames: int = 1

    @classmethod
    def wait(cls, frames: int, reason: str = "wait") -> "Decision":
        return cls(reason=reason, settle_frames=frames)

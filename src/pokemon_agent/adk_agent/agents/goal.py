from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_MAIN_GOAL = "Complete Pokemon Red"
MAX_GOAL_TEXT_LENGTH = 500


def normalize_goal(value: Any, *, default_main: str = DEFAULT_MAIN_GOAL) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    main = _goal_text(source.get("main"), allow_empty=False, fallback=default_main)
    sub = _goal_text(source.get("sub"), allow_empty=True, fallback="")
    return {"main": main, "sub": sub}


def goal_from_main(main_goal: str | None) -> dict[str, str]:
    if main_goal is None:
        return normalize_goal(None)
    return {
        "main": _goal_text(main_goal, allow_empty=False),
        "sub": "",
    }


@dataclass
class GoalUpdateBuffer:
    baseline: dict[str, str] = field(default_factory=lambda: normalize_goal(None))
    activity: list[dict[str, Any]] = field(default_factory=list)

    def begin(self, goal: Any) -> None:
        self.baseline = normalize_goal(goal)
        self.activity.clear()

    def update(self, main_goal: str, sub_goal: str) -> dict[str, Any]:
        if self.activity:
            return {
                "phase": "goal_update",
                "changed": False,
                "duplicate_ignored": True,
                "goal": dict(self.activity[0]["goal"]),
            }
        goal = {
            "main": _goal_text(main_goal, allow_empty=False),
            "sub": _goal_text(sub_goal, allow_empty=True),
        }
        result = {
            "phase": "goal_update",
            "changed": goal != self.baseline,
            "goal": goal,
        }
        self.activity.append(dict(result))
        return result

    @property
    def last_update(self) -> dict[str, Any] | None:
        return dict(self.activity[-1]) if self.activity else None


def build_update_goal_tool(buffer: GoalUpdateBuffer) -> Callable[..., dict[str, Any]]:
    def update_goal(main_goal: str, sub_goal: str) -> dict[str, Any]:
        """Replace the volatile main/sub goal snapshot when the milestone changes."""

        return buffer.update(main_goal, sub_goal)

    return update_goal


def _goal_text(
    value: Any,
    *,
    allow_empty: bool,
    fallback: str | None = None,
) -> str:
    text = " ".join(str(value if value is not None else fallback or "").split())
    if not text and not allow_empty:
        raise ValueError("main goal must not be empty")
    return text[:MAX_GOAL_TEXT_LENGTH]

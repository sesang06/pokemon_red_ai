from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class GameAreaEnvironment(Protocol):
    def game_area(self) -> Any:
        """Return PyBoy.game_area()."""

    def game_area_collision(self) -> Any:
        """Return PyBoy.game_area_collision()."""


def format_game_area_watch(env: GameAreaEnvironment) -> str:
    try:
        matrix = env.game_area()
    except Exception as exc:
        return f"game_area unavailable: {type(exc).__name__}: {exc}"

    return _format_matrix("pyboy.game_area()", matrix, cell_width=4)


def format_game_area_collision_watch(env: GameAreaEnvironment) -> str:
    try:
        matrix = env.game_area_collision()
    except Exception as exc:
        return f"game_area_collision unavailable: {type(exc).__name__}: {exc}"

    return _format_matrix("pyboy.game_area_collision()", matrix, cell_width=2)


def _format_matrix(title: str, matrix: Any, cell_width: int) -> str:
    rows = _to_rows(matrix)
    if not rows:
        return f"{title}\n(empty)"

    width = max(len(row) for row in rows)
    height = len(rows)
    lines = [
        title,
        f"shape: {height} rows x {width} cols",
        "",
    ]

    for y, row in enumerate(rows):
        cells = " ".join(_format_cell(value, cell_width) for value in row)
        lines.append(f"{y:02d}: {cells}")

    return "\n".join(lines)


def _to_rows(matrix: Any) -> list[list[int]]:
    if hasattr(matrix, "tolist"):
        matrix = matrix.tolist()

    if isinstance(matrix, memoryview):
        matrix = matrix.tolist()

    if not isinstance(matrix, Sequence) or isinstance(matrix, (bytes, bytearray, str)):
        return [[_to_int(matrix)]]

    rows: list[list[int]] = []
    for row in matrix:
        if isinstance(row, Sequence) and not isinstance(row, (bytes, bytearray, str)):
            rows.append([_to_int(value) for value in row])
        else:
            rows.append([_to_int(row)])
    return rows


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _format_cell(value: int, width: int) -> str:
    return f"{value:{width}d}"

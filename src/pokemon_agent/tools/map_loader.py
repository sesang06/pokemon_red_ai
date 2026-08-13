from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MapData:
    name: str
    walkable: tuple[tuple[int, ...], ...]


def load_map(path: Path) -> MapData:
    data = json.loads(path.read_text(encoding="utf-8"))
    walkable = tuple(tuple(int(value) for value in row) for row in data["walkable"])
    return MapData(name=str(data["name"]), walkable=walkable)

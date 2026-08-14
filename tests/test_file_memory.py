from __future__ import annotations

import json
from pathlib import Path

import pytest

from pokemon_agent.memory.file_memory import FileLongTermMemory, map_memory_key


def test_file_memory_initializes_missing_file(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    assert store.read_all() == {"version": 1, "updated_at": None, "items": {}}


def test_file_memory_round_trips_only_map_memory(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    written = store.remember_map("Pallet Town", "north exit is useful", source="test")

    assert written == "map:Pallet Town"
    assert store.get_map("Pallet Town")["value"] == "north exit is useful"
    assert list(store.items()) == ["map:Pallet Town"]


def test_map_memory_key_is_always_map_scoped() -> None:
    assert map_memory_key("  Oak's   Lab ") == "map:Oak's Lab"
    assert map_memory_key("map:Pallet Town") == "map:Pallet Town"
    with pytest.raises(ValueError):
        map_memory_key(" ")


def test_file_memory_overwrites_the_single_entry_for_a_map(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")

    store.remember_map("Oak's Lab", "starter table is north", source="test")
    store.remember_map("Oak's Lab", "starter table is north; exit is south", source="test")

    assert list(store.items()) == ["map:Oak's Lab"]
    assert store.get_map("Oak's Lab")["value"] == "starter table is north; exit is south"


def test_file_memory_hides_legacy_non_map_keys(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "event:legacy": {"value": "old event"},
                    "map:Pallet Town": {"value": "north exit"},
                },
            }
        ),
        encoding="utf-8",
    )

    assert FileLongTermMemory(path).items() == {
        "map:Pallet Town": {"value": "north exit"},
    }

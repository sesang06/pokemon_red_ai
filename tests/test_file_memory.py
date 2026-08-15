from __future__ import annotations

import json
from pathlib import Path

import pytest

from pokemon_agent.memory.file_memory import FileLongTermMemory, memory_key


def test_file_memory_initializes_missing_file(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    assert store.read_all() == {"version": 1, "updated_at": None, "items": {}}


def test_file_memory_round_trips_all_supported_memory_types(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    written = {
        memory_type: store.remember(memory_type, name, value, source="test")
        for memory_type, name, value in (
            ("map", "Pallet Town", "north exit is useful"),
            ("npc", "Professor Oak", "runs the Pokemon lab"),
            ("pokemon", "Bulbasaur", "selected as the starter"),
            ("event", "starter_selection", "completed in Oak's Lab"),
        )
    }

    assert written == {
        "map": "map:Pallet Town",
        "npc": "npc:Professor Oak",
        "pokemon": "pokemon:Bulbasaur",
        "event": "event:starter_selection",
    }
    assert store.get("pokemon", "Bulbasaur")["value"] == "selected as the starter"
    assert set(store.items()) == set(written.values())


def test_memory_key_validates_type_name_and_prefix() -> None:
    assert memory_key("map", "  Oak's   Lab ") == "map:Oak's Lab"
    assert memory_key("npc", "npc:Professor Oak") == "npc:Professor Oak"
    with pytest.raises(ValueError):
        memory_key("item", "Potion")
    with pytest.raises(ValueError):
        memory_key("pokemon", " ")
    with pytest.raises(ValueError):
        memory_key("npc", "map:Oak's Lab")


def test_file_memory_overwrites_the_single_entry_for_an_identity(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")

    store.remember("npc", "Professor Oak", "stands in the lab", source="test")
    store.remember("npc", "Professor Oak", "offers a starter in the lab", source="test")

    assert list(store.items()) == ["npc:Professor Oak"]
    assert store.get("npc", "Professor Oak")["value"] == "offers a starter in the lab"


def test_file_memory_keeps_supported_namespaces_and_hides_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "event:starter_selection": {"value": "starter chosen"},
                    "item:Potion": {"value": "unsupported namespace"},
                    "map:Pallet Town": {"value": "north exit"},
                },
            }
        ),
        encoding="utf-8",
    )

    assert FileLongTermMemory(path).items() == {
        "event:starter_selection": {"value": "starter chosen"},
        "map:Pallet Town": {"value": "north exit"},
    }

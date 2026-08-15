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


def test_file_memory_writes_a_valid_batch_atomically(tmp_path: Path, monkeypatch) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    writes = 0
    real_write = store._write_store

    def count_write(payload):
        nonlocal writes
        writes += 1
        real_write(payload)

    monkeypatch.setattr(store, "_write_store", count_write)
    keys = store.remember_many(
        [
            {"memory_type": "map", "name": "Oak's Lab", "value": "exit at [5,11]"},
            {"memory_type": "npc", "name": "Professor Oak", "value": "stands near [5,2]"},
        ],
        source="test",
    )

    assert writes == 1
    assert keys == ["map:Oak's Lab", "npc:Professor Oak"]
    assert set(store.items()) == set(keys)


def test_file_memory_rejects_entire_invalid_batch_before_writing(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    store.remember("map", "Pallet Town", "north exit", source="test")

    with pytest.raises(ValueError):
        store.remember_many(
            [
                {"memory_type": "npc", "name": "Professor Oak", "value": "runs the lab"},
                {"memory_type": "item", "name": "Potion", "value": "unsupported"},
            ]
        )

    assert list(store.items()) == ["map:Pallet Town"]


def test_file_memory_batch_merge_preserves_distinct_existing_facts(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    store.remember("map", "Oak's Lab", "Professor Oak stands near [5,2].", source="test")

    store.remember_many(
        [
            {
                "memory_type": "map",
                "name": "Oak's Lab",
                "value": "The exit is at [5,11].",
                "operation": "append",
            }
        ],
        source="result_interpreter",
    )

    assert store.get("map", "Oak's Lab")["value"] == (
        "Professor Oak stands near [5,2].\nThe exit is at [5,11]."
    )


def test_file_memory_replace_operation_overwrites_existing_value(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    store.remember("npc", "Professor Oak", "outdated location", source="test")

    store.remember_many(
        [
            {
                "memory_type": "npc",
                "name": "Professor Oak",
                "value": "Runs the Pokemon Lab in Pallet Town.",
                "operation": "replace",
            }
        ],
        source="result_interpreter",
    )

    assert store.get("npc", "Professor Oak")["value"] == "Runs the Pokemon Lab in Pallet Town."


def test_file_memory_rejects_invalid_operation_before_writing(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    store.remember("map", "Pallet Town", "north exit", source="test")

    with pytest.raises(ValueError, match="memory operation"):
        store.remember_many(
            [
                {
                    "memory_type": "map",
                    "name": "Pallet Town",
                    "value": "south exit",
                    "operation": "delete",
                }
            ]
        )

    assert store.get("map", "Pallet Town")["value"] == "north exit"


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

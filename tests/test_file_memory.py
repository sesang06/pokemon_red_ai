from __future__ import annotations

from pathlib import Path

from pokemon_agent.memory.file_memory import FileLongTermMemory, memory_keys_for_state, normalize_memory_key


def test_file_memory_initializes_missing_file(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    assert store.read_all() == {"version": 1, "updated_at": None, "items": {}}


def test_file_memory_round_trips_map_keyword_and_goal_keys(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    store.remember("map:Pallet Town", "north exit is useful", source="test")
    store.remember("keyword:oak lab", "starter location", source="test")
    store.remember("goal:main", "leave Pallet Town", source="test")

    assert store.get("map:Pallet Town")["value"] == "north exit is useful"
    assert store.get("keyword:oak_lab")["value"] == "starter location"
    assert store.get("goal:main")["value"] == "leave Pallet Town"
    assert "keyword:oak_lab" in store.search("starter")


def test_memory_keys_for_state_uses_map_goal_and_keywords() -> None:
    state = {
        "objective": "safe_loop explore oak_lab",
        "observation": {
            "state": {
                "map_name": "Pallet Town",
                "dialog_text": "Professor Oak is waiting.",
            }
        },
    }

    assert memory_keys_for_state(state)[:3] == ["goal:main", "map:Pallet Town", "keyword:safe_loop"]
    assert normalize_memory_key("keyword:oak lab") == "keyword:oak_lab"


def test_file_memory_preserves_extended_namespaces(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")

    written = store.remember_many(
        [
            {"key": "event:oak_pokeball", "value": "verified event"},
            {"key": "npc:professor_oak", "value": "Oak is in the lab"},
            {"key": "item:poke_ball", "value": "inventory verified"},
            {"key": "strategy:oak_event", "value": "verify inventory"},
            {"key": "failure:oak_repeat", "value": "dialog is not success"},
            {"key": "episode:oak_001", "value": "completed"},
        ]
    )

    assert written == [
        "event:oak_pokeball",
        "npc:professor_oak",
        "item:poke_ball",
        "strategy:oak_event",
        "failure:oak_repeat",
        "episode:oak_001",
    ]

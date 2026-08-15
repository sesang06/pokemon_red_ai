from __future__ import annotations

import inspect
from pathlib import Path

from pokemon_agent.adk_agent.agents.memory_tools import (
    build_save_memory_tool,
    build_search_memory_tool,
)
from pokemon_agent.memory.file_memory import FileLongTermMemory


def test_memory_tools_use_validated_type_and_name_as_storage_identity(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    activity: list[dict] = []
    published: list[dict] = []
    search_memory = build_search_memory_tool(store, activity=activity, on_activity=published.append)
    save_memory = build_save_memory_tool(store, activity=activity, on_activity=published.append)

    saved = save_memory("npc", "Professor Oak", "Runs the lab in Pallet Town.")
    loaded = search_memory("npc", "Professor Oak")

    assert save_memory.__name__ == "save_memory"
    assert search_memory.__name__ == "search_memory"
    assert list(inspect.signature(save_memory).parameters) == ["memory_type", "name", "value"]
    assert list(inspect.signature(search_memory).parameters) == ["memory_type", "name"]
    assert saved["key"] == "npc:Professor Oak"
    assert saved["memory_type"] == "npc"
    assert loaded["item"]["value"] == "Runs the lab in Pallet Town."
    assert [entry["tool"] for entry in activity] == ["save_memory", "search_memory"]
    assert [entry["tool"] for entry in published] == ["save_memory", "search_memory"]
    assert published[-1]["item"]["value"] == "Runs the lab in Pallet Town."
    assert list(store.items()) == ["npc:Professor Oak"]


def test_search_memory_marks_duplicate_identity_with_final_response_instruction(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    store.remember("map", "Oak's Lab", "starter table is north", source="test")
    activity: list[dict] = []
    search_memory = build_search_memory_tool(store, activity=activity)

    first = search_memory("map", "Oak's Lab")
    duplicate = search_memory("map", "Oak's Lab")

    assert first.get("already_searched") is None
    assert duplicate["already_searched"] is True
    assert duplicate["item"] == first["item"]
    assert "emit the required final JSON response now" in duplicate["next_step"]

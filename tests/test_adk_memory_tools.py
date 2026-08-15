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

    saved = save_memory(
        entries=[
            {"memory_type": "npc", "name": "Professor Oak", "value": "Runs the lab in Pallet Town."},
            {"memory_type": "event", "name": "starter_selection", "value": "Oak offers a starter."},
        ]
    )
    loaded = search_memory(
        queries=[
            {"memory_type": "npc", "name": "Professor Oak"},
            {"memory_type": "event", "name": "starter_selection"},
        ]
    )

    assert save_memory.__name__ == "save_memory"
    assert search_memory.__name__ == "search_memory"
    assert list(inspect.signature(save_memory).parameters) == ["entries"]
    assert list(inspect.signature(search_memory).parameters) == ["queries"]
    assert saved["keys"] == ["npc:Professor Oak", "event:starter_selection"]
    assert saved["written_count"] == 2
    assert loaded["keys"] == ["npc:Professor Oak", "event:starter_selection"]
    assert loaded["found_count"] == 2
    assert loaded["results"][0]["item"]["value"] == "Runs the lab in Pallet Town."
    assert [entry["tool"] for entry in activity] == ["save_memory", "search_memory"]
    assert [entry["tool"] for entry in published] == ["save_memory", "search_memory"]
    assert published[-1]["results"][0]["item"]["value"] == "Runs the lab in Pallet Town."
    assert set(store.items()) == {"npc:Professor Oak", "event:starter_selection"}


def test_search_memory_deduplicates_batch_and_requests_final_response(tmp_path: Path) -> None:
    store = FileLongTermMemory(tmp_path / "memory.json")
    store.remember("map", "Oak's Lab", "starter table is north", source="test")
    activity: list[dict] = []
    search_memory = build_search_memory_tool(store, activity=activity)

    result = search_memory(
        queries=[
            {"memory_type": "map", "name": "Oak's Lab"},
            {"memory_type": "map", "name": "Oak's Lab"},
        ]
    )

    assert result["keys"] == ["map:Oak's Lab"]
    assert result["found_count"] == 1
    assert result["results"][0]["item"]["value"] == "starter table is north"
    assert "emit the required final JSON response" in result["next_step"]

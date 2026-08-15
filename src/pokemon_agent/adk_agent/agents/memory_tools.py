from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pokemon_agent.memory.file_memory import FileLongTermMemory, memory_key


MemoryTool = Callable[..., dict[str, Any]]
def search_memory_entries(
    store: FileLongTermMemory,
    queries: list[dict[str, str]],
) -> dict[str, Any]:
    if not queries:
        raise ValueError("at least one memory query is required")
    items = store.items()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        if not isinstance(query, dict):
            raise ValueError("each memory query must be an object")
        key = memory_key(str(query.get("memory_type", "")), str(query.get("name", "")))
        if key in seen:
            continue
        seen.add(key)
        memory_type, name = key.split(":", 1)
        item = items.get(key)
        results.append(
            {
                "memory_type": memory_type,
                "name": name,
                "key": key,
                "found": item is not None,
                "item": item,
            }
        )
    return {
        "phase": "memory_search",
        "keys": [entry["key"] for entry in results],
        "found_count": sum(bool(entry["found"]) for entry in results),
        "results": results,
    }


def save_memory_entries(
    store: FileLongTermMemory,
    entries: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    previous_items = store.items()
    normalized_entries: list[dict[str, Any]] = []
    operations: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each memory entry must be an object")
        normalized = dict(entry)
        normalized["operation"] = str(entry.get("operation", "append")).strip().lower()
        key = memory_key(str(entry.get("memory_type", "")), str(entry.get("name", "")))
        operations[key] = normalized["operation"]
        normalized_entries.append(normalized)

    keys = store.remember_many(normalized_entries, source=source)
    items = store.items()
    results = [
        {
            "memory_type": key.split(":", 1)[0],
            "name": key.split(":", 1)[1],
            "key": key,
            "operation": operations[key],
            "written": True,
            "previous_item": previous_items.get(key),
            "item": items.get(key),
        }
        for key in keys
    ]
    return {
        "phase": "memory_write",
        "keys": keys,
        "written_count": len(keys),
        "results": results,
    }


def build_search_memory_tool(
    store: FileLongTermMemory,
    *,
    activity: list[dict[str, Any]] | None = None,
    on_activity: Callable[[dict[str, Any]], None] | None = None,
) -> MemoryTool:
    def search_memory(queries: list[dict[str, str]]) -> dict[str, Any]:
        """Load multiple map, NPC, Pokemon, or event memories in one call."""

        result = search_memory_entries(store, queries)
        result["next_step"] = (
            "All requested identities have now been searched. Do not call search_memory again; "
            "continue reasoning and emit the required final JSON response."
        )
        if activity is not None:
            activity.append({"tool": "search_memory", **result})
        if on_activity is not None:
            on_activity({"tool": "search_memory", **result})
        return result

    return search_memory


def build_save_memory_tool(
    store: FileLongTermMemory,
    *,
    source: str = "result_interpreter",
    activity: list[dict[str, Any]] | None = None,
    on_activity: Callable[[dict[str, Any]], None] | None = None,
) -> MemoryTool:
    def save_memory(entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Atomically append to or replace multiple map, NPC, Pokemon, or event memories."""

        result = save_memory_entries(store, entries, source=source)
        if activity is not None:
            activity.append({"tool": "save_memory", **result})
        if on_activity is not None:
            on_activity({"tool": "save_memory", **result})
        return result

    return save_memory

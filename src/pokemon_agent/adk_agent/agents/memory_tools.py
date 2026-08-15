from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pokemon_agent.memory.file_memory import FileLongTermMemory, memory_key


MemoryTool = Callable[..., dict[str, Any]]
MemoryType = Literal["map", "npc", "pokemon", "event"]


def search_memory_entry(
    store: FileLongTermMemory,
    memory_type: MemoryType,
    name: str,
) -> dict[str, Any]:
    key = memory_key(memory_type, name)
    item = store.get(memory_type, name)
    return {
        "phase": "memory_search",
        "memory_type": key.split(":", 1)[0],
        "name": key.split(":", 1)[1],
        "key": key,
        "found": item is not None,
        "item": item,
    }


def save_memory_entry(
    store: FileLongTermMemory,
    memory_type: MemoryType,
    name: str,
    value: str,
    *,
    source: str,
) -> dict[str, Any]:
    key = store.remember(memory_type, name, value, source=source)
    return {
        "phase": "memory_write",
        "memory_type": key.split(":", 1)[0],
        "name": key.split(":", 1)[1],
        "key": key,
        "written": True,
        "item": store.get(memory_type, name),
    }


def build_search_memory_tool(
    store: FileLongTermMemory,
    *,
    activity: list[dict[str, Any]] | None = None,
    on_activity: Callable[[dict[str, Any]], None] | None = None,
) -> MemoryTool:
    def search_memory(memory_type: MemoryType, name: str) -> dict[str, Any]:
        """Load one map, NPC, Pokemon, or event memory by its canonical name."""

        requested_key = memory_key(memory_type, name)
        if activity is not None:
            previous = next(
                (
                    entry
                    for entry in reversed(activity)
                    if entry.get("tool") == "search_memory" and entry.get("key") == requested_key
                ),
                None,
            )
            if previous is not None:
                result = {
                    key: previous.get(key)
                    for key in ("phase", "memory_type", "name", "key", "found", "item")
                }
                result.update(
                    {
                        "already_searched": True,
                        "next_step": (
                            "Do not call search_memory again for this identity during this invocation. "
                            "Use the returned memory and emit the required final JSON response now."
                        ),
                    }
                )
                activity.append({"tool": "search_memory", **result})
                if on_activity is not None:
                    on_activity({"tool": "search_memory", **result})
                return result

        result = search_memory_entry(store, memory_type, name)
        result["next_step"] = (
            "This identity has now been searched for this invocation. Do not repeat the same search; "
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
    def save_memory(memory_type: MemoryType, name: str, value: str) -> dict[str, Any]:
        """Save one consolidated map, NPC, Pokemon, or event memory under a validated type and name."""

        result = save_memory_entry(store, memory_type, name, value, source=source)
        if activity is not None:
            activity.append({"tool": "save_memory", **result})
        if on_activity is not None:
            on_activity({"tool": "save_memory", **result})
        return result

    return save_memory

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LONG_TERM_MEMORY_PATH = Path(__file__).resolve().parents[3] / "data" / "long_term_memory.json"
MEMORY_NAMESPACES = {"map", "event", "npc", "item", "goal", "strategy", "failure", "episode", "keyword"}


class FileLongTermMemory:
    """Small JSON key/value memory store for the ADK control agents."""

    def __init__(self, path: Path | str = DEFAULT_LONG_TERM_MEMORY_PATH):
        self.path = Path(path)

    def read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_store()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty_store()

        if not isinstance(loaded, dict):
            return _empty_store()
        items = loaded.get("items")
        if not isinstance(items, dict):
            loaded["items"] = {}
        loaded.setdefault("version", 1)
        loaded.setdefault("updated_at", None)
        return loaded

    def items(self) -> dict[str, dict[str, Any]]:
        raw_items = self.read_all().get("items", {})
        return {str(key): dict(value) for key, value in raw_items.items() if isinstance(value, dict)}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.items().get(normalize_memory_key(key))

    def get_many(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        items = self.items()
        result: dict[str, dict[str, Any]] = {}
        for key in keys:
            normalized = normalize_memory_key(key)
            if normalized in items:
                result[normalized] = items[normalized]
        return result

    def search(self, query: str, *, limit: int = 20) -> dict[str, dict[str, Any]]:
        needle = query.strip().lower()
        if not needle:
            return dict(list(self.items().items())[:limit])

        matches: dict[str, dict[str, Any]] = {}
        for key, item in self.items().items():
            value = str(item.get("value", ""))
            if needle in key.lower() or needle in value.lower():
                matches[key] = item
            if len(matches) >= limit:
                break
        return matches

    def relevant_for_state(self, state: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
        keys = memory_keys_for_state(state)
        exact = self.get_many(keys)
        if len(exact) >= limit:
            return {"keys": list(exact.keys()), "items": dict(list(exact.items())[:limit])}

        merged = dict(exact)
        observation = state.get("observation", {})
        game_state = observation.get("state", {}) if isinstance(observation, dict) else {}
        action_plan = state.get("active_action_plan", {}) if isinstance(state.get("active_action_plan"), dict) else {}
        action = action_plan.get("action", {}) if isinstance(action_plan.get("action"), dict) else {}
        queries = [
            str(state.get("objective", "")),
            str(state.get("current_goal", {}).get("id", "")) if isinstance(state.get("current_goal"), dict) else "",
            str(action.get("type", "")),
            str(action.get("reason", "")),
            str(game_state.get("map_name", "")),
        ]
        all_items = self.items()
        query_tokens = set(_keyword_tokens(" ".join(queries)))
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for key, item in all_items.items():
            searchable = f"{key} {item.get('value', '')}".lower()
            score = sum(1 for token in query_tokens if token in searchable)
            if key.startswith(("failure:", "strategy:")) and score:
                score += 3
            if score:
                ranked.append((score, key, item))
        for _score, key, item in sorted(ranked, key=lambda entry: (-entry[0], entry[1])):
            merged.setdefault(key, item)
            if len(merged) >= limit:
                break
        return {"keys": list(merged.keys()), "items": merged}

    def remember(self, key: str, value: Any, *, source: str = "result_interpreter") -> str:
        written = self.remember_many([{"key": key, "value": value, "source": source}], source=source)
        return written[0] if written else normalize_memory_key(key)

    def remember_many(self, writes: list[dict[str, Any]], *, source: str = "result_interpreter") -> list[str]:
        valid_writes: list[tuple[str, Any, str]] = []
        for write in writes:
            if not isinstance(write, dict):
                continue
            raw_key = str(write.get("key", "")).strip()
            if not raw_key:
                continue
            raw_value = write.get("value")
            if raw_value is None:
                continue
            valid_writes.append(
                (
                    normalize_memory_key(raw_key),
                    raw_value,
                    str(write.get("source") or source),
                )
            )

        if not valid_writes:
            return []

        store = self.read_all()
        items = store.setdefault("items", {})
        now = _now_iso()
        written: list[str] = []
        for key, value, item_source in valid_writes:
            items[key] = {
                "value": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True),
                "updated_at": now,
                "source": item_source,
            }
            written.append(key)
        store["version"] = 1
        store["updated_at"] = now
        self._write_store(store)
        return written

    def _write_store(self, store: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(store, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def memory_keys_for_state(state: dict[str, Any]) -> list[str]:
    observation = state.get("observation", {})
    state_info = observation.get("state", {}) if isinstance(observation, dict) else {}
    keys = ["goal:main"]

    map_name = state_info.get("map_name")
    if map_name:
        keys.append(f"map:{map_name}")

    objective = str(state.get("objective", ""))
    current_goal = state.get("current_goal", {}) if isinstance(state.get("current_goal"), dict) else {}
    action_plan = state.get("active_action_plan", {}) if isinstance(state.get("active_action_plan"), dict) else {}
    action = action_plan.get("action", {}) if isinstance(action_plan.get("action"), dict) else {}
    if current_goal.get("id"):
        keys.append(f"goal:{current_goal['id']}")
    if action.get("reason"):
        action_key = _slug(str(action["reason"]))
        keys.extend(
            [
                f"strategy:{action_key}",
                f"failure:{action_key}",
                f"event:{action_key}",
            ]
        )
    dialog_text = str(state_info.get("dialog_text") or "")
    for token in _keyword_tokens(f"{objective} {dialog_text}"):
        keys.append(f"keyword:{token}")

    deduped: list[str] = []
    for key in keys:
        normalized = normalize_memory_key(key)
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def normalize_memory_key(key: str) -> str:
    key = re.sub(r"\s+", " ", key.strip())
    if ":" not in key:
        return f"keyword:{_slug(key)}"

    prefix, value = key.split(":", 1)
    prefix = prefix.strip().lower()
    value = value.strip()
    if prefix == "map":
        return f"map:{value}"
    if prefix in MEMORY_NAMESPACES:
        return f"{prefix}:{_slug(value)}"
    return f"keyword:{_slug(value)}"


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "items": {}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _keyword_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum() or char == "_":
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return [_slug(token) for token in tokens[:8] if len(token) >= 3]


def _slug(value: str) -> str:
    chars = [char if char.isalnum() or char == "_" else "_" for char in value.strip().lower()]
    slug = re.sub(r"_+", "_", "".join(chars)).strip("_")
    return slug or "unknown"

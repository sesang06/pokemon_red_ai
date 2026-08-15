from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LONG_TERM_MEMORY_PATH = Path(__file__).resolve().parents[3] / "data" / "long_term_memory.json"
MEMORY_TYPES = ("map", "npc", "pokemon", "event")
MEMORY_WRITE_OPERATIONS = ("append", "replace")


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
        else:
            loaded["items"] = {
                str(key): value
                for key, value in items.items()
                if _is_supported_memory_key(str(key)) and isinstance(value, dict)
            }
        loaded.setdefault("version", 1)
        loaded.setdefault("updated_at", None)
        return loaded

    def items(self) -> dict[str, dict[str, Any]]:
        raw_items = self.read_all().get("items", {})
        return {str(key): dict(value) for key, value in raw_items.items() if isinstance(value, dict)}

    def get(self, memory_type: str, name: str) -> dict[str, Any] | None:
        return self.items().get(memory_key(memory_type, name))

    def remember(
        self,
        memory_type: str,
        name: str,
        value: Any,
        *,
        source: str = "result_interpreter",
    ) -> str:
        return self.remember_many(
            [{"memory_type": memory_type, "name": name, "value": value}],
            source=source,
        )[0]

    def remember_many(
        self,
        entries: list[dict[str, Any]],
        *,
        source: str = "result_interpreter",
    ) -> list[str]:
        """Validate and persist multiple memories with one atomic file replacement."""

        validated: list[tuple[str, Any, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("each memory entry must be an object")
            key = memory_key(str(entry.get("memory_type", "")), str(entry.get("name", "")))
            value = entry.get("value")
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError("memory value must not be empty")
            operation = str(entry.get("operation", "replace")).strip().lower()
            if operation not in MEMORY_WRITE_OPERATIONS:
                raise ValueError(
                    f"memory operation must be one of: {', '.join(MEMORY_WRITE_OPERATIONS)}"
                )
            validated.append((key, value, operation))
        if not validated:
            raise ValueError("at least one memory entry is required")

        store = self.read_all()
        items = store.setdefault("items", {})
        now = _now_iso()
        keys: list[str] = []
        for key, value, operation in validated:
            if operation == "append":
                existing = items.get(key)
                existing_value = existing.get("value") if isinstance(existing, dict) else None
                value = _merge_memory_values(existing_value, value)
            items[key] = {
                "value": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True),
                "updated_at": now,
                "source": source,
            }
            if key not in keys:
                keys.append(key)
        store["version"] = 1
        store["updated_at"] = now
        self._write_store(store)
        return keys

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


def memory_key(memory_type: str, name: str) -> str:
    normalized_type = str(memory_type).strip().lower()
    if normalized_type not in MEMORY_TYPES:
        raise ValueError(f"memory_type must be one of: {', '.join(MEMORY_TYPES)}")

    normalized = re.sub(r"\s+", " ", str(name).strip())
    prefix, separator, remainder = normalized.partition(":")
    if separator and prefix.lower() in MEMORY_TYPES:
        if prefix.lower() != normalized_type:
            raise ValueError("memory name prefix does not match memory_type")
        normalized = remainder.strip()
    if not normalized:
        raise ValueError("memory name must not be empty")
    return f"{normalized_type}:{normalized}"


def _is_supported_memory_key(key: str) -> bool:
    prefix, separator, name = key.partition(":")
    return bool(separator and name.strip() and prefix.lower() in MEMORY_TYPES)


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "items": {}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _merge_memory_values(existing: Any, incoming: Any) -> Any:
    if existing is None:
        return incoming
    existing_text = existing if isinstance(existing, str) else json.dumps(existing, ensure_ascii=False, sort_keys=True)
    incoming_text = incoming if isinstance(incoming, str) else json.dumps(incoming, ensure_ascii=False, sort_keys=True)
    existing_text = existing_text.strip()
    incoming_text = incoming_text.strip()
    if not existing_text or incoming_text == existing_text or incoming_text in existing_text:
        return existing_text or incoming_text
    if existing_text in incoming_text:
        return incoming_text
    return f"{existing_text}\n{incoming_text}"

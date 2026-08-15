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
        key = memory_key(memory_type, name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("memory value must not be empty")
        store = self.read_all()
        items = store.setdefault("items", {})
        now = _now_iso()
        items[key] = {
            "value": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True),
            "updated_at": now,
            "source": source,
        }
        store["version"] = 1
        store["updated_at"] = now
        self._write_store(store)
        return key

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

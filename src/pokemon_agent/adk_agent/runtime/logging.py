from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass
class DateGroupedActionLogger:
    root: Path = Path("logs") / "actions"
    clock: Callable[[], datetime] = datetime.now

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._lock = Lock()

    def append(self, entry: dict[str, Any]) -> Path:
        timestamp = self.clock()
        dated_dir = self.root / timestamp.strftime("%Y%m%d")
        path = dated_dir / "actions.jsonl"
        record = {
            "timestamp": timestamp.isoformat(timespec="milliseconds"),
            **entry,
        }

        with self._lock:
            dated_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, default=str))
                file.write("\n")

        return path

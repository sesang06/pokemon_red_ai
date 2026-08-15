from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.apps.app import EventsCompactionConfig
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from pokemon_agent.adk_agent.runtime.history import trim_events_to_recent_turns


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ADK_SESSION_DB_PATH = PROJECT_ROOT / "data" / "adk_sessions.db"
ADK_WEB_APP_NAME = "adk_agent"
DEFAULT_ADK_USER_ID = "user"
DEFAULT_COMPACTION_INTERVAL = 5
DEFAULT_COMPACTION_OVERLAP_SIZE = 1
DEFAULT_COMPACTION_TOKEN_THRESHOLD = 50_000
DEFAULT_EVENT_RETENTION_SIZE = 8


def build_events_compaction_config(
    *,
    interval: int = DEFAULT_COMPACTION_INTERVAL,
    overlap_size: int = DEFAULT_COMPACTION_OVERLAP_SIZE,
    token_threshold: int = DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    event_retention_size: int = DEFAULT_EVENT_RETENTION_SIZE,
) -> EventsCompactionConfig:
    return EventsCompactionConfig(
        compaction_interval=interval,
        overlap_size=overlap_size,
        token_threshold=token_threshold,
        event_retention_size=event_retention_size,
    )


class ContextFilteringSqliteSessionService(SqliteSessionService):
    """Persist full sessions while filtering the context returned to a runner."""

    def __init__(self, db_path: str | Path, *, prior_turn_limit: int | None) -> None:
        path = Path(db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(path))
        self.prior_turn_limit = None if prior_turn_limit is None else max(0, int(prior_turn_limit))

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Any = None,
    ) -> Any:
        session = await super().get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            config=config,
        )
        if session is not None and config is None:
            if self.prior_turn_limit is not None:
                trim_events_to_recent_turns(session.events, max_turns=self.prior_turn_limit)
            _strip_media_from_context_events(session.events)
        return session


def _strip_media_from_context_events(events: list[Any]) -> int:
    from google.genai import types

    removed = 0
    for event in events:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None)
        if not parts:
            continue
        kept_parts = []
        event_removed = 0
        for part in parts:
            if getattr(part, "inline_data", None) or getattr(part, "file_data", None):
                removed += 1
                event_removed += 1
            else:
                kept_parts.append(part)
        if event_removed:
            kept_parts.append(
                types.Part.from_text(
                    text=(
                        f"[{event_removed} prior media image(s) omitted from model context. "
                        "Use only the latest images attached to the current request.]"
                    )
                )
            )
            content.parts = kept_parts
    return removed

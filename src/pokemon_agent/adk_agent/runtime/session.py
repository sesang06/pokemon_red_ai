from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ADK_SESSION_DB_PATH = PROJECT_ROOT / "data" / "adk_sessions.db"
ADK_WEB_APP_NAME = "adk_agent"
DEFAULT_ADK_USER_ID = "user"
DEFAULT_COMPACTION_INTERVAL = 10
DEFAULT_COMPACTION_OVERLAP_SIZE = 1
DEFAULT_COMPACTION_TOKEN_THRESHOLD = 10_000
DEFAULT_EVENT_RETENTION_SIZE = 8
DEFAULT_COMPACTION_MODEL = "gemini-2.5-flash-lite"


class ThinkingDisabledGemini(Gemini):
    """Gemini backend used only for low-cost event compaction."""

    @staticmethod
    def configure_request(llm_request: LlmRequest) -> None:
        llm_request.config.thinking_config = types.ThinkingConfig(
            thinking_budget=0,
            include_thoughts=False,
        )

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self.configure_request(llm_request)
        async for response in super().generate_content_async(llm_request, stream=stream):
            yield response


def build_events_compaction_config(
    *,
    interval: int = DEFAULT_COMPACTION_INTERVAL,
    overlap_size: int = DEFAULT_COMPACTION_OVERLAP_SIZE,
    token_threshold: int = DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    event_retention_size: int = DEFAULT_EVENT_RETENTION_SIZE,
    model: str = DEFAULT_COMPACTION_MODEL,
) -> EventsCompactionConfig:
    return EventsCompactionConfig(
        summarizer=LlmEventSummarizer(llm=ThinkingDisabledGemini(model=model)),
        compaction_interval=interval,
        overlap_size=overlap_size,
        token_threshold=token_threshold,
        event_retention_size=event_retention_size,
    )


class ContextFilteringSqliteSessionService(SqliteSessionService):
    """Persist full sessions while filtering the context returned to a runner."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(path))

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

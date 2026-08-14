from __future__ import annotations

from typing import Any


RAW_HISTORY_TURNS = 20
RESULT_INTERPRETER_PRIOR_TURNS = 0


def trim_session_to_recent_turns(
    session_service: Any,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    max_turns: int,
) -> int:
    """Keep complete events belonging to the most recent user turns."""
    sessions = getattr(session_service, "sessions", None)
    if not isinstance(sessions, dict):
        return 0

    session = sessions.get(app_name, {}).get(user_id, {}).get(session_id)
    events = getattr(session, "events", None)
    if not isinstance(events, list):
        return 0

    return trim_events_to_recent_turns(events, max_turns=max_turns)


def trim_events_to_recent_turns(events: list[Any], *, max_turns: int) -> int:
    user_turn_starts = [index for index, event in enumerate(events) if _is_user_event(event)]
    if len(user_turn_starts) <= max_turns:
        return 0

    first_kept_index = len(events) if max_turns <= 0 else user_turn_starts[-max_turns]
    removed = first_kept_index
    del events[:first_kept_index]
    return removed


def _is_user_event(event: Any) -> bool:
    if str(getattr(event, "author", "")).lower() == "user":
        return True
    content = getattr(event, "content", None)
    return str(getattr(content, "role", "")).lower() == "user"

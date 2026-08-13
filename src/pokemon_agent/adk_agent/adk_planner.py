from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from pokemon_agent.adk_agent.planning import compact_state_for_prompt

DEFAULT_ADK_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are a Pokemon Red control planner running inside Google ADK.
Return only one JSON object. Do not use markdown.
You may choose exactly one of these action schemas:
{"type":"move_to_screen_tile","target_x":10,"target_y":8,"max_steps":1,"accept_nearest":true,"reason":"short reason"}
{"type":"press_button","button":"a","frames":4,"after_frames":8,"reason":"short reason"}
{"type":"execute_actions","actions":[{"button":"a","frames":4,"after_frames":8}],"reason":"short reason"}
{"type":"step_frames","frames":10,"reason":"short reason"}
Allowed buttons are a, b, start, select, left, right, up, down.
Prefer small, reversible actions. For overworld movement, prefer move_to_screen_tile.
Use target_x/target_y in current game_area tile coordinates, where x is 0..19 and y is 0..17.
"""


@dataclass
class GoogleAdkPlanner:
    model: str = DEFAULT_ADK_MODEL
    include_screenshot: bool = False
    app_name: str = "pokemon_red_adk"
    user_id: str = "pokemon-agent"
    session_id: str = "pokemon-red-safe-loop"
    temperature: float = 0.2
    max_output_tokens: int = 300

    def __post_init__(self) -> None:
        try:
            from google.adk.agents import Agent
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types
        except ModuleNotFoundError as exc:
            raise RuntimeError('Install Google ADK first: python -m pip install -e ".[dev,vision,memory]"') from exc

        generate_config = types.GenerateContentConfig(
            temperature=self.temperature,
            maxOutputTokens=self.max_output_tokens,
            responseMimeType="application/json",
        )
        self.session_service = InMemorySessionService()
        self.agent = Agent(
            name="pokemon_red_planner",
            model=self.model,
            description="Chooses one safe Pokemon Red emulator action as JSON.",
            instruction=SYSTEM_PROMPT,
            generate_content_config=generate_config,
        )
        self.runner = Runner(
            agent=self.agent,
            app_name=self.app_name,
            session_service=self.session_service,
        )
        self._session_created = False

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        include_screenshot: bool = False,
    ) -> "GoogleAdkPlanner":
        return cls(
            model=model or os.environ.get("POKEMON_AGENT_ADK_MODEL", DEFAULT_ADK_MODEL),
            include_screenshot=include_screenshot,
        )

    def plan(self, state: dict[str, Any]) -> dict[str, Any] | None:
        return asyncio.run(self.plan_async(state))

    async def plan_async(self, state: dict[str, Any]) -> dict[str, Any] | None:
        await self._ensure_session()
        content = self._content_for_state(state)
        final_text = ""
        async for event in self.runner.run_async(
            user_id=self.user_id,
            session_id=self.session_id,
            new_message=content,
        ):
            text = _event_text(event)
            if text:
                final_text = text
            if event.is_final_response() and text:
                final_text = text
        action = _parse_json_object(final_text)
        if isinstance(action, dict):
            action.setdefault("source", "adk")
            return action
        return None

    async def _ensure_session(self) -> None:
        if self._session_created:
            return
        await self.session_service.create_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        self._session_created = True

    def _content_for_state(self, state: dict[str, Any]) -> Any:
        from google.genai import types

        text = json.dumps(compact_state_for_prompt(state), ensure_ascii=False, indent=2)
        parts: list[Any] = [types.Part.from_text(text=text)]

        screenshot = state.get("observation", {}).get("screenshot", {})
        screenshot_base64 = screenshot.get("base64")
        if self.include_screenshot and screenshot_base64:
            parts.append(
                types.Part.from_bytes(
                    data=base64.b64decode(screenshot_base64),
                    mime_type="image/png",
                )
            )

        return types.Content(role="user", parts=parts)


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    if content is None:
        output = getattr(event, "output", None)
        return "" if output is None else str(output)

    parts = getattr(content, "parts", None) or []
    text_parts = [str(part.text) for part in parts if getattr(part, "text", None)]
    return "\n".join(text_parts)


def _parse_json_object(content: str) -> Any:
    cleaned = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    elif not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None

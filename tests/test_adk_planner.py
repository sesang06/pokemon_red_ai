from __future__ import annotations

import asyncio
import base64
import json

from google.genai import types
from google.adk.models.llm_request import LlmRequest

from pokemon_agent.adk_agent.agents.planner.agent import (
    GoogleAdkPlanner,
    parse_planner_response,
)
from pokemon_agent.adk_agent.agents.interpreter.agent import GoogleAdkResultInterpreter
from pokemon_agent.adk_agent.agents.planner.schema import (
    compact_state_for_prompt,
    normalize_action_plan,
    sanitize_action,
)
from pokemon_agent.adk_agent.agents.shared import (
    MAX_AUTOMATIC_FUNCTION_CALLS,
    event_finish_reason,
    event_text,
    invalid_response_error,
    parse_json_object,
)
from pokemon_agent.adk_agent.runtime.session import (
    ADK_WEB_APP_NAME,
    DEFAULT_COMPACTION_INTERVAL,
    DEFAULT_COMPACTION_MODEL,
    DEFAULT_COMPACTION_OVERLAP_SIZE,
    DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_EVENT_RETENTION_SIZE,
    ContextFilteringSqliteSessionService,
    ThinkingDisabledGemini,
    build_events_compaction_config,
)
from pokemon_agent.input_contract import (
    MAX_BUTTONS_PER_ACTION,
    MAX_MOVE_PATH_STEPS,
    MAX_MOVE_WAYPOINTS,
    MAX_WORLD_NAVIGATION_SEGMENTS,
)


class FakePart:
    text = '{"type":"buttons","buttons":["wait"]}'
    thought = False


class FakeContent:
    parts = [FakePart()]


class FakeEvent:
    content = FakeContent()


def test_adk_event_text_extracts_content_parts() -> None:
    assert event_text(FakeEvent()) == '{"type":"buttons","buttons":["wait"]}'


def test_adk_event_text_ignores_private_thinking_parts() -> None:
    class ThoughtPart:
        text = "Checking the current map and reachable targets."
        thought = True

    class MixedEvent:
        content = type("Content", (), {"parts": [ThoughtPart(), FakePart()]})()

    event = MixedEvent()
    assert event_text(event) == '{"type":"buttons","buttons":["wait"]}'


def test_adk_parse_json_object_handles_markdown_fence() -> None:
    assert parse_json_object('```json\n{"type":"buttons","buttons":["a"]}\n```') == {
        "type": "buttons",
        "buttons": ["a"],
    }


def test_planner_response_recovers_labeled_gemini_action_output() -> None:
    response = """화면 설명: 오박사 연구실 내부가 보인다.
현재 위치: Oak's Lab [9,5]
생각 요약: 출구 방향으로 길게 이동한다.
action: {"type":"move","target":[5,5],"reason":"출구로 이동"}"""

    assert parse_planner_response(response) == {
        "screen_description": "오박사 연구실 내부가 보인다.",
        "current_location": "Oak's Lab [9,5]",
        "thought_summary": "출구 방향으로 길게 이동한다.",
        "action": {"type": "move", "target": [5, 5], "reason": "출구로 이동"},
    }


def test_planner_response_rejects_non_action_json() -> None:
    assert parse_planner_response('{"goal":"complete_pokemon_red"}') is None


def test_planner_response_uses_final_action_json_after_prose_quotes_an_earlier_action() -> None:
    response = '''The previous rejected action was {"type":"buttons","buttons":["wait"]}.
Now return the final response:
{"screen_description":"battle","current_location":"Route 1 (10, 33)","thought_summary":"Advance the fainted dialog.","action":{"type":"buttons","buttons":["a"],"reason":"advance_fainted_dialog"}}'''

    assert parse_planner_response(response) == {
        "screen_description": "battle",
        "current_location": "Route 1 (10, 33)",
        "thought_summary": "Advance the fainted dialog.",
        "action": {
            "type": "buttons",
            "buttons": ["a"],
            "reason": "advance_fainted_dialog",
        },
    }


def test_adk_planner_budget_and_truncation_diagnostics() -> None:
    class TruncatedEvent:
        finish_reason = "MAX_TOKENS"

    assert GoogleAdkPlanner.max_output_tokens == 4096
    assert event_finish_reason(TruncatedEvent()) == "MAX_TOKENS"
    assert invalid_response_error('{"goal":"complete_pokemon_red"', finish_reason="MAX_TOKENS") == (
        "invalid_json_response (finish_reason=MAX_TOKENS, chars=30)"
    )


def test_native_adk_agent_backends_use_medium_thinking_without_thought_output() -> None:
    planner = GoogleAdkPlanner()

    assert planner.agent.generate_content_config.thinking_config.thinking_level == "MEDIUM"
    assert planner.agent.generate_content_config.thinking_config.include_thoughts is False
    assert (
        planner.agent.generate_content_config.automatic_function_calling.maximum_remote_calls
        == MAX_AUTOMATIC_FUNCTION_CALLS
    )
    assert MAX_AUTOMATIC_FUNCTION_CALLS == 4
    assert planner.agent.generate_content_config.response_mime_type is None
    planner_tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in planner.agent.tools
    }
    assert planner_tool_names == {"search_memory", "save_memory"}

    interpreter = GoogleAdkResultInterpreter()
    assert interpreter.agent.generate_content_config.thinking_config.thinking_level == "MEDIUM"
    assert interpreter.agent.generate_content_config.thinking_config.include_thoughts is False
    assert (
        interpreter.agent.generate_content_config.automatic_function_calling.maximum_remote_calls
        == MAX_AUTOMATIC_FUNCTION_CALLS
    )
    assert interpreter.agent.generate_content_config.response_mime_type is None
    interpreter_tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in interpreter.agent.tools
    }
    assert interpreter_tool_names == {"search_memory", "save_memory", "update_goal"}


def test_turn_compaction_runs_at_ten_turn_intervals_with_one_turn_overlap() -> None:
    async def exercise_compaction():
        from google.adk.agents import Agent
        from google.adk.apps.app import App
        from google.adk.apps.compaction import _run_compaction_for_sliding_window
        from google.adk.events import Event
        from google.adk.events.event_actions import EventActions, EventCompaction
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        class RecordingSummarizer:
            def __init__(self) -> None:
                self.ranges: list[list[str]] = []

            async def maybe_summarize_events(self, *, events):
                invocation_ids = list(dict.fromkeys(event.invocation_id for event in events))
                self.ranges.append(invocation_ids)
                return Event(
                    author="user",
                    invocation_id=Event.new_id(),
                    timestamp=events[-1].timestamp + 0.1,
                    actions=EventActions(
                        compaction=EventCompaction(
                            start_timestamp=events[0].timestamp,
                            end_timestamp=events[-1].timestamp,
                            compacted_content=types.Content(
                                role="model",
                                parts=[types.Part.from_text(text="summary")],
                            ),
                        )
                    ),
                )

        summarizer = RecordingSummarizer()
        config = build_events_compaction_config()
        config.summarizer = summarizer
        app = App(
            name="test_app",
            root_agent=Agent(name="test_agent", model="gemini-3.5-flash"),
            events_compaction_config=config,
        )
        service = InMemorySessionService()
        session = await service.create_session(app_name="test_app", user_id="user", session_id="session")

        async def add_turn(turn: int) -> None:
            invocation_id = f"inv-{turn}"
            await service.append_event(
                session,
                Event(
                    author="user",
                    invocation_id=invocation_id,
                    timestamp=float(turn * 10),
                    content=types.Content(role="user", parts=[types.Part.from_text(text=f"user-{turn}")]),
                ),
            )
            await service.append_event(
                session,
                Event(
                    author="test_agent",
                    invocation_id=invocation_id,
                    timestamp=float(turn * 10 + 1),
                    content=types.Content(role="model", parts=[types.Part.from_text(text=f"agent-{turn}")]),
                ),
            )

        async def compact():
            events = [
                event
                async for event in _run_compaction_for_sliding_window(
                    app,
                    session,
                    service,
                )
            ]
            for event in events:
                await service.append_event(session, event)
            return events

        for turn in range(1, 10):
            await add_turn(turn)
        before_interval = await compact()
        await add_turn(10)
        first = await compact()
        for turn in range(11, 20):
            await add_turn(turn)
        before_second_interval = await compact()
        await add_turn(20)
        second = await compact()
        return summarizer.ranges, before_interval, first, before_second_interval, second

    ranges, before_interval, first, before_second_interval, second = asyncio.run(exercise_compaction())

    assert DEFAULT_COMPACTION_INTERVAL == 10
    assert DEFAULT_COMPACTION_OVERLAP_SIZE == 1
    assert DEFAULT_COMPACTION_TOKEN_THRESHOLD == 10_000
    assert DEFAULT_EVENT_RETENTION_SIZE == 8
    assert before_interval == []
    assert before_second_interval == []
    assert len(first) == 1
    assert len(second) == 1
    assert ranges == [
        [f"inv-{turn}" for turn in range(1, 11)],
        [f"inv-{turn}" for turn in range(10, 21)],
    ]


def test_compaction_uses_flash_lite_with_thinking_disabled() -> None:
    config = build_events_compaction_config()
    llm = config.summarizer._llm
    request = LlmRequest(model=llm.model)

    assert isinstance(llm, ThinkingDisabledGemini)
    assert llm.model == DEFAULT_COMPACTION_MODEL == "gemini-2.5-flash-lite"

    llm.configure_request(request)

    assert request.config.thinking_config.thinking_budget == 0
    assert request.config.thinking_config.include_thoughts is False


def test_shared_sqlite_preserves_full_trace_and_filters_prior_media(tmp_path) -> None:
    async def exercise_services():
        from google.adk.events import Event
        from google.adk.sessions.sqlite_session_service import SqliteSessionService
        from google.genai import types

        database_path = tmp_path / "adk_sessions.db"
        filtered = ContextFilteringSqliteSessionService(database_path)
        session = await filtered.create_session(app_name=ADK_WEB_APP_NAME, user_id="user", session_id="planner")
        for turn in range(12):
            await filtered.append_event(
                session,
                Event(
                    author="user",
                    content=types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=f"user-{turn}"),
                            types.Part.from_bytes(data=f"image-{turn}".encode(), mime_type="image/png"),
                        ],
                    ),
                ),
            )
            await filtered.append_event(
                session,
                Event(
                    author="planner",
                    content=types.Content(role="model", parts=[types.Part.from_text(text=f"agent-{turn}")]),
                ),
            )

        filtered_session = await filtered.get_session(app_name=ADK_WEB_APP_NAME, user_id="user", session_id="planner")
        dev_ui_reader = SqliteSessionService(str(database_path))
        full_session = await dev_ui_reader.get_session(app_name=ADK_WEB_APP_NAME, user_id="user", session_id="planner")
        return filtered_session, full_session

    filtered_session, full_session = asyncio.run(exercise_services())
    assert len([event for event in filtered_session.events if event.author == "user"]) == 12
    assert len([event for event in full_session.events if event.author == "user"]) == 12
    assert not any(
        getattr(part, "inline_data", None)
        for event in filtered_session.events
        for part in (event.content.parts if event.content else [])
    )
    assert any(
        getattr(part, "inline_data", None)
        for event in full_session.events
        for part in (event.content.parts if event.content else [])
    )


def test_planner_payload_keeps_only_two_transitions_and_excludes_duplicate_histories() -> None:
    payload = compact_state_for_prompt(
        {
            "history_summary": "Earlier progress summary",
            "action_history": [{"step": step} for step in range(10)],
            "transition_history": [{"step": step} for step in range(10)],
            "interpretation": {"summary": "Duplicate task result"},
            "observation": {"state": {}, "state_events": []},
        }
    )

    assert payload["recent_state_transitions"] == [{"step": 8}, {"step": 9}]
    assert "recent_actions" not in payload
    assert "history_summary" not in payload
    assert "last_interpretation" not in payload
    assert "state_events" not in payload
    assert "available_story_tasks" not in payload
    assert "safe_neighbor_world_cells" not in payload


def test_planner_payload_removes_observation_blobs_from_history() -> None:
    observation_blob = {
        "state": {"map_name": "Oak's Lab", "position": {"x": 5, "y": 6}},
        "screenshot": {"base64": "x" * 100_000},
        "visible_world_cells": [[{"x": x, "y": y, "walkable": True} for x in range(10)] for y in range(9)],
    }
    payload = compact_state_for_prompt(
        {
            "observation": {
                "state": {"map_name": "Oak's Lab", "position": {"x": 6, "y": 6}},
                "walk_area_collision": [[1 for _ in range(10)] for _ in range(9)],
            },
            "transition_history": [
                {
                    "step": 7,
                    "task_id": "explore",
                    "action": {"type": "move", "target": [6, 6], "reason": "explore"},
                    "before": observation_blob["state"],
                    "after": observation_blob["state"],
                    "state_changes": ["position_changed"],
                    "action_status": "continue",
                }
            ],
            "execution_report": {
                "task_id": "explore",
                "action": {"type": "move", "target": [6, 6]},
                "before_state": observation_blob,
                "after_state": observation_blob,
                "result": {"stop_reason": "target_reached", "after_observation": observation_blob},
            },
        }
    )

    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert len(serialized) < 10_000
    assert "base64" not in serialized
    assert "before_observation" not in serialized
    assert "after_observation" not in serialized
    transition = payload["recent_state_transitions"][0]
    assert transition["state"]["position"] == {"x": 5, "y": 6}
    assert "before" not in transition
    assert "after" not in transition


def test_planner_payload_compacts_last_action_plan_and_outcome() -> None:
    payload = compact_state_for_prompt(
        {
            "observation": {"state": {"map_name": "Oak's Lab", "position": {"x": 8, "y": 4}}},
            "active_action_plan": {
                "action": {"type": "move", "target": [8, 4], "reason": "approach_starter"},
                "status": "single_action_complete",
            },
            "action_outcome": {
                "status": "single_action_complete",
                "action_result": "success",
                "state_changes": ["position_changed"],
                "reason": "single_action_complete",
            },
            "state_diff": {"meaningful": True},
        }
    )

    assert payload["last_action_plan"] == {
        "action": {"type": "move", "target": [8, 4]},
        "status": "single_action_complete",
    }
    assert payload["last_action_outcome"] == {
        "status": "single_action_complete",
        "action_result": "success",
        "state_changed": True,
        "state_changes": ["position_changed"],
        "reason": "single_action_complete",
    }
    assert "current_task" not in json.dumps(payload)
    assert "task_result" not in json.dumps(payload)


def test_planner_payload_uses_canonical_mode_dependent_game_state() -> None:
    base_state = {
        "map_id": 40,
        "map_name": "Oak's Lab",
        "position": {"x": 8, "y": 4},
        "position_detail": {"tile": {"x": 8, "y": 4}},
        "summary": "duplicate summary",
        "dialog_open": False,
        "in_battle": False,
        "dialog": {"open": False, "text": None},
        "battle": {"active": False, "turns": 64},
        "menu": {"active": False, "selection": 1},
        "map": {"id": 40, "name": "Oak's Lab", "collision_ptr": 1234},
        "counts": {"party": 0, "items": 0, "badges": 0, "warps": 2},
        "items": [],
        "party": [],
        "badges": [],
        "money": 3000,
        "flags": {"oak_asked_to_choose_mon": True, "got_starter": False, "has_warps": True},
    }
    payload = compact_state_for_prompt({"mode": "overworld", "observation": {"state": base_state}})
    compact = payload["state"]

    assert compact == {
        "map_id": 40,
        "map_name": "Oak's Lab",
        "position": {"x": 8, "y": 4},
        "mode": "overworld",
        "dialog_open": False,
        "in_battle": False,
        "menu_open": False,
        "controls_locked": False,
        "counts": {"party": 0, "items": 0, "badges": 0},
        "money": 3000,
        "flags": {"oak_asked_to_choose_mon": True, "got_starter": False},
    }

    locked_state = dict(base_state)
    locked_state["raw"] = {"controls_locked": True}
    locked_payload = compact_state_for_prompt(
        {"mode": "overworld", "observation": {"state": locked_state}}
    )
    assert locked_payload["state"]["controls_locked"] is True

    dialog_state = dict(base_state)
    dialog_state.update(
        {
            "dialog_open": True,
            "dialog_text": "Choose a Pokemon.",
            "dialog": {"open": True, "text": "Choose a Pokemon.", "box_detected": True},
        }
    )
    dialog_payload = compact_state_for_prompt({"mode": "dialog", "observation": {"state": dialog_state}})
    assert dialog_payload["state"]["dialog"] == {"text": "Choose a Pokemon.", "box_detected": True}
    assert "battle" not in dialog_payload["state"]
    assert "menu" not in dialog_payload["state"]

    closed_dialog_payload = compact_state_for_prompt(
        {
            "mode": "overworld",
            "observation": {"state": base_state},
            "transition_history": [
                {
                    "before": {
                        **base_state,
                        "dialog_open": True,
                        "dialog_text": "Which POKEMON do you want?",
                    },
                    "after": base_state,
                }
            ],
        }
    )
    assert closed_dialog_payload["last_dialog"] == {
        "text": "Which POKEMON do you want?",
        "status": "recently_closed",
    }

    battle_state = dict(base_state)
    battle_state.update(
        {
            "in_battle": True,
            "battle": {
                "active": True,
                "kind": 1,
                "type": 2,
                "turns": 3,
                "opponent": {
                    "species": "Rattata",
                    "level": 4,
                    "hp": 9,
                    "max_hp": 15,
                    "status": "OK",
                    "types": ["Normal"],
                    "moves": ["Tackle"],
                    "move_pp": [34],
                },
            },
            "party": [{"species": "Bulbasaur", "level": 5, "hp": 19, "max_hp": 19}],
        }
    )
    battle_payload = compact_state_for_prompt({"mode": "battle", "observation": {"state": battle_state}})
    assert battle_payload["state"]["battle"] == {
        "kind": 1,
        "type": 2,
        "turns": 3,
        "opponent": {
            "species": "Rattata",
            "level": 4,
            "hp": 9,
            "max_hp": 15,
            "status": "OK",
            "types": ["Normal"],
        },
        "party": [{"species": "Bulbasaur", "level": 5, "hp": 19, "max_hp": 19}],
    }
    assert "dialog" not in battle_payload["state"]
    assert "menu" not in battle_payload["state"]

    menu_state = dict(base_state)
    menu_state["menu"] = {"active": True, "selection": 2, "start_menu_cursor": 1}
    menu_payload = compact_state_for_prompt({"mode": "menu", "observation": {"state": menu_state}})
    assert menu_payload["state"]["menu"] == {"selection": 2, "start_menu_cursor": 1}
    assert "dialog" not in menu_payload["state"]
    assert "battle" not in menu_payload["state"]


def test_planner_payload_uses_memory_tools_and_limits_navigation_context() -> None:
    observation = {
        "state": {"map_name": "Oak's Lab", "map_id": 40, "position": {"x": 8, "y": 5}},
        "walk_area_collision": [[1 for _ in range(10)] for _ in range(9)],
        "world_map": {
            "map_id": 40,
            "map_name": "Oak's Lab",
            "known_tiles": 60,
            "walkable_tiles": 20,
            "visited_tiles": 4,
            "frontier_tiles": [{"x": index, "y": 4, "distance": index} for index in range(10)],
            "nearest_screen_tile": {"world_x": 9, "world_y": 4, "distance": 1},
        },
    }
    non_navigation = compact_state_for_prompt(
        {
            "goal": {"main": "Complete Pokemon Red", "sub": "Choose a starter"},
            "mode": "dialog",
            "observation": observation,
            "active_action_plan": {
                "action": {"type": "buttons", "buttons": ["a", "wait"], "reason": "complete_dialog"},
                "status": "active",
            },
        }
    )
    assert "world_map" not in non_navigation
    assert "visible_walk_area" not in non_navigation

    navigation = compact_state_for_prompt(
        {
            "goal": {"main": "Complete Pokemon Red", "sub": "Leave Oak's Lab"},
            "mode": "overworld",
            "observation": observation,
            "active_action_plan": {
                "action": {"type": "move", "target": [8, 4], "reason": "move_to_starter"},
                "status": "active",
            },
        }
    )
    assert "long_term_memory" not in navigation
    assert len(navigation["world_map"]["frontier_tiles"]) == 4
    assert navigation["world_map"]["nearest_frontier_world_cell"] == {"x": 9, "y": 4, "distance": 1}
    assert navigation["navigation"]["coordinate_system"] == "current_map_world"
    assert navigation["navigation"]["player"] == [8, 5]
    assert navigation["navigation"]["visible_world_bounds"] == {
        "min_x": 4,
        "max_x": 13,
        "min_y": 1,
        "max_y": 9,
    }
    assert navigation["navigation"]["remote_targets_allowed"] is True
    assert navigation["navigation"]["automatic_segment_replanning"] is True
    assert navigation["navigation"]["max_path_steps_per_segment"] == MAX_MOVE_PATH_STEPS
    assert navigation["navigation"]["max_segments_per_move"] == MAX_WORLD_NAVIGATION_SEGMENTS
    assert [12, 9, 8] in navigation["navigation"]["reachable_targets"]
    assert [13, 9, 9] not in navigation["navigation"]["reachable_targets"]
    assert all(
        target[2] <= MAX_MOVE_PATH_STEPS
        for target in navigation["navigation"]["reachable_targets"]
    )
    assert "visible_walk_area" not in navigation
    assert "safe_neighbor_world_cells" not in navigation


def test_adk_planner_content_attaches_only_current_screenshot_and_overlay() -> None:
    planner = GoogleAdkPlanner.__new__(GoogleAdkPlanner)
    planner.include_screenshot = True
    state = {
        "observation": {
            "state": {"position": {"x": 5, "y": 6}},
            "screenshot": {"base64": base64.b64encode(b"current-screen").decode("ascii")},
            "screenshot_overlay": {"base64": base64.b64encode(b"current-overlay").decode("ascii")},
        },
    }

    content = planner._content_for_state(state)
    media_parts = [part for part in content.parts if getattr(part, "inline_data", None)]

    assert len(media_parts) == 2
    assert media_parts[0].inline_data.data == b"current-screen"
    assert media_parts[1].inline_data.data == b"current-overlay"
    assert all(
        part.media_resolution.level
        == types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM
        for part in media_parts
    )
    assert "\n  " not in content.parts[0].text


def test_sanitize_action_rejects_out_of_bounds_target() -> None:
    assert sanitize_action({"type": "move", "target": [256, 4]}) is None


def test_sanitize_action_accepts_button_arrays() -> None:
    action = sanitize_action(
        {
            "type": "buttons",
            "buttons": ["a", "wait"],
        }
    )

    assert action == {
        "type": "buttons",
        "buttons": ["a", "wait"],
        "reason": "adk_buttons_a",
    }


def test_sanitize_action_rejects_unsupported_action_shapes() -> None:
    assert sanitize_action({"type": "unsupported_action", "button": "a"}) is None
    assert sanitize_action({"type": "buttons", "buttons": [{"button": "a"}]}) is None
    assert sanitize_action({"type": "move", "target": {"x": 1, "y": 3}}) is None


def test_sanitize_action_rejects_oversized_button_arrays_instead_of_truncating() -> None:
    buttons = ["wait"] * (MAX_BUTTONS_PER_ACTION + 1)

    assert sanitize_action({"type": "buttons", "buttons": buttons}) is None


def test_sanitize_action_accepts_move_target() -> None:
    assert sanitize_action({"type": "move", "target": [1, 3]}) == {
        "type": "move",
        "target": [1, 3],
        "reason": "adk_move",
    }


def test_sanitize_action_accepts_ordered_move_waypoints() -> None:
    assert sanitize_action(
        {
            "type": "move",
            "waypoints": [[4, 5], (8, 9)],
            "target": [12, 9],
            "reason": "follow_known_route",
        }
    ) == {
        "type": "move",
        "waypoints": [[4, 5], [8, 9]],
        "target": [12, 9],
        "reason": "follow_known_route",
    }


def test_sanitize_action_rejects_invalid_move_waypoints() -> None:
    assert sanitize_action(
        {"type": "move", "waypoints": [[4, 5, 6]], "target": [12, 9]}
    ) is None
    assert sanitize_action(
        {
            "type": "move",
            "waypoints": [[index, 5] for index in range(MAX_MOVE_WAYPOINTS + 1)],
            "target": [12, 9],
        }
    ) is None


def test_normalize_action_plan_accepts_one_shot_action_contract() -> None:
    plan = normalize_action_plan(
        {
            "action": {
                "type": "buttons",
                "buttons": ["a", "wait", "a"],
                "reason": "advance_dialog_twice",
            },
        }
    )

    assert plan == {
        "action": {
            "type": "buttons",
            "buttons": ["a", "wait", "a"],
            "reason": "advance_dialog_twice",
        },
        "status": "active",
    }


def test_normalize_action_plan_rejects_task_without_an_action() -> None:
    assert normalize_action_plan({"task": {"task_id": "legacy"}}) is None

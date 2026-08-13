from __future__ import annotations

from pokemon_agent.adk_agent.adk_planner import _event_text, _parse_json_object
from pokemon_agent.adk_agent.planning import sanitize_planned_action


class FakePart:
    text = '{"type":"step_frames","frames":5}'


class FakeContent:
    parts = [FakePart()]


class FakeEvent:
    content = FakeContent()


def test_adk_event_text_extracts_content_parts() -> None:
    assert _event_text(FakeEvent()) == '{"type":"step_frames","frames":5}'


def test_adk_parse_json_object_handles_markdown_fence() -> None:
    assert _parse_json_object('```json\n{"type":"press_button","button":"a"}\n```') == {
        "type": "press_button",
        "button": "a",
    }


def test_sanitize_planned_action_rejects_out_of_bounds_target() -> None:
    assert sanitize_planned_action({"type": "move_to_screen_tile", "target_x": 40, "target_y": 8}) is None


def test_sanitize_planned_action_limits_execute_actions() -> None:
    action = sanitize_planned_action(
        {
            "type": "execute_actions",
            "actions": [
                {"button": "a", "frames": 4},
                {"button": "b", "frames": 4},
                {"button": "left", "frames": 4},
                {"button": "right", "frames": 4},
                {"button": "start", "frames": 4},
            ],
        }
    )

    assert action is not None
    assert len(action["actions"]) == 4

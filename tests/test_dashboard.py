from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from pokemon_agent.dashboard.events import LiveEventHub
from pokemon_agent.dashboard.models import observation_state
from pokemon_agent.dashboard.server import DashboardRuntimeStateStore, create_dashboard_app


def sample_observation(*, x: int = 5, y: int = 6) -> dict:
    image = {"format": "png", "width": 160, "height": 144, "base64": "iVBORw0KGgo="}
    return {
        "frame_index": 120,
        "tool_step_index": 3,
        "screenshot": image,
        "screenshot_overlay": image,
        "state": {
            "map_id": 0,
            "map_name": "Pallet Town",
            "position": {"x": x, "y": y},
            "facing": "down",
            "mode": "explore",
            "dialog_open": False,
            "dialog_text": None,
            "in_battle": False,
            "party": [
                {
                    "species": "Charmander",
                    "species_id": 4,
                    "internal_species_id": 0xB0,
                    "level": 5,
                    "hp": 20,
                    "max_hp": 20,
                }
            ],
            "items": [{"name": "Potion", "quantity": 2, "item_id": 20}],
            "badges": [],
            "money": 3000,
        },
        "state_events": [
            {"type": "position_changed", "from": {"x": x - 1, "y": y}, "to": {"x": x, "y": y}}
        ],
        "visible_world_cells": [[{"x": x, "y": y, "walkable": True}]],
        "walk_area_collision": [[1]],
        "world_map": {"map_id": 0, "map_name": "Pallet Town"},
        "ram": {"player_x": x, "player_y": y},
    }


def test_observation_state_serializes_real_game_and_species_data() -> None:
    live = observation_state(
        sample_observation(),
        ticker={"fps": 60.0, "snapshot_hz": 10.0, "ticker_alive": True},
    )

    assert live["game"]["map_name"] == "Pallet Town"
    assert live["game"]["position"] == {"x": 5, "y": 6}
    assert live["game"]["party"][0]["species_id"] == 4
    assert live["game"]["party"][0]["internal_species_id"] == 0xB0
    assert live["game"]["screenshot"]["base64"] == "iVBORw0KGgo="
    assert live["debug"]["ram"] == {"player_x": 5, "player_y": 6}


def test_live_event_hub_sends_snapshot_typed_event_and_state_delta() -> None:
    async def scenario() -> None:
        hub = LiveEventHub(state_hz=30)
        hub.publish_observation(sample_observation(x=4, y=6))
        subscription = hub.subscribe()
        initial = await subscription.queue.get()
        assert initial["kind"] == "snapshot"

        await asyncio.sleep(0.04)
        hub.publish_observation(sample_observation())
        messages = [await asyncio.wait_for(subscription.queue.get(), timeout=1) for _ in range(2)]
        assert {message["kind"] for message in messages} == {"event", "state_delta"}
        event = next(message["event"] for message in messages if message["kind"] == "event")
        delta = next(message for message in messages if message["kind"] == "state_delta")
        assert event["type"] == "STATE_CHANGED"
        assert event["payload"]["to"] == {"x": 5, "y": 6}
        assert delta["changes"]["game"]["position"] == {"x": 5}
        assert "items" not in delta["changes"]["game"]
        hub.unsubscribe(subscription)

    asyncio.run(scenario())


def test_dashboard_websocket_reconnect_starts_with_latest_snapshot(tmp_path: Path) -> None:
    hub = LiveEventHub()
    hub.publish_observation(sample_observation())
    app = create_dashboard_app(hub, frontend_dist=tmp_path / "missing-dist")

    with TestClient(app) as client:
        assert client.get("/api/health").json()["frame_index"] == 120
        with client.websocket_connect("/ws/live") as socket:
            message = socket.receive_json()
            assert message["kind"] == "snapshot"
            assert message["state"]["game"]["map_name"] == "Pallet Town"
        with client.websocket_connect("/ws/live") as socket:
            reconnected = socket.receive_json()
            assert reconnected["state"]["emulator"]["frame_index"] == 120


def test_dashboard_serves_packaged_frontend() -> None:
    app = create_dashboard_app(LiveEventHub())

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Pokemon Red Runtime Debugger" in response.text


def test_runtime_state_store_publishes_file_state_and_memory() -> None:
    published = []

    class FileStore:
        def publish(self, state, *, phase):
            published.append((state, phase))

    class MemoryStore:
        def items(self):
            return {"map:Pallet Town": {"value": "Oak lab known", "updated_at": "2026-08-15T00:00:00Z"}}

    hub = LiveEventHub()
    store = DashboardRuntimeStateStore(FileStore(), hub, memory_store=MemoryStore())
    store.publish(
        {
            "goal": {"main": "Complete Pokemon Red", "sub": "Choose a starter"},
            "step_count": 1,
            "action_outcome": {"status": "single_action_complete"},
        },
        phase="interpreted",
    )

    snapshot = hub.snapshot()["state"]
    assert published[0][1] == "interpreted"
    assert snapshot["agent"]["goal"] == {
        "main": "Complete Pokemon Red",
        "sub": "Choose a starter",
    }
    assert snapshot["agent"]["current_step"] == 1
    assert snapshot["agent"]["max_steps"] is None
    assert snapshot["memory"]["recent"][0]["key"] == "map:Pallet Town"


def test_memory_activity_immediately_prioritizes_latest_loaded_value() -> None:
    hub = LiveEventHub()
    items = {
        "map:Pallet Town": {
            "value": "newer by write time",
            "updated_at": "2026-08-15T02:00:00Z",
            "source": "result_interpreter",
        },
        "npc:Professor Oak": {
            "value": "recently loaded content",
            "updated_at": "2026-08-15T01:00:00Z",
            "source": "result_interpreter",
        },
    }

    hub.publish_memory_activity(
        items,
        {"tool": "search_memory", "keys": ["npc:Professor Oak"]},
    )
    hub.publish_memory_snapshot(items)

    memory = hub.snapshot()["state"]["memory"]
    assert memory["last_activity"]["type"] == "search_memory"
    assert memory["recent"][0] == {
        "key": "npc:Professor Oak",
        "value": "recently loaded content",
        "source": "result_interpreter",
        "updated_at": "2026-08-15T01:00:00Z",
    }


def test_trace_events_only_expose_structured_public_fields() -> None:
    hub = LiveEventHub()
    hub.publish_trace(
        {
            "agent": "pokemon_red_planning_agent",
            "phase": "planning_done",
            "step": 4,
            "screen_description": "태초마을의 필드 화면",
            "current_location": "Pallet Town (7, 5)",
            "thought_summary": "이동 가능한 좌표로 탐색을 이어갑니다.",
            "memory_keys_read": ["map:Pallet Town"],
            "action_plan": {
                "action": {"type": "move", "target": [7, 5]},
            },
        }
    )

    snapshot = hub.snapshot()
    event = snapshot["events"][-2]
    assert all(item["type"] != "THINKING_SUMMARY" for item in snapshot["events"])
    thinking_state = snapshot["state"]["agent"]["thinking"]
    assert thinking_state["agent"] == "planner"
    assert thinking_state["status"] == "complete"
    assert thinking_state["summary"] == "이동 가능한 좌표로 탐색을 이어갑니다."
    assert thinking_state["updated_at"]
    assert event["type"] == "PLAN_CREATED"
    assert event["payload"]["screen_description"] == "태초마을의 필드 화면"
    assert event["payload"]["current_location"] == "Pallet Town (7, 5)"
    assert event["payload"]["thought_summary"] == "이동 가능한 좌표로 탐색을 이어갑니다."
    assert event["payload"]["decision"] == {"type": "move", "target": [7, 5]}
    assert "repeat_until" not in event["payload"]
    assert "max_repeats" not in event["payload"]


def test_private_thinking_trace_is_not_exposed() -> None:
    hub = LiveEventHub()
    before = hub.snapshot()
    hub.publish_trace(
        {
            "agent": "pokemon_red_planning_agent",
            "phase": "planning_thinking",
            "step": 2,
            "thinking_summary": "Checking the map and reachable targets",
        }
    )
    after = hub.snapshot()

    assert after["events"] == before["events"] == []
    assert after["state"]["agent"]["thinking"] == before["state"]["agent"]["thinking"]

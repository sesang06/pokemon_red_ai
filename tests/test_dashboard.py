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
            "objective": "safe_loop",
            "current_goal": {"id": "safe_loop", "status": "in_progress"},
            "step_count": 1,
            "action_outcome": {"status": "single_action_complete"},
        },
        phase="interpreted",
    )

    snapshot = hub.snapshot()["state"]
    assert published[0][1] == "interpreted"
    assert snapshot["agent"]["task"]["id"] == "safe_loop"
    assert snapshot["memory"]["recent"][0]["key"] == "map:Pallet Town"


def test_trace_events_only_expose_structured_public_fields() -> None:
    hub = LiveEventHub()
    hub.publish_trace(
        {
            "agent": "pokemon_red_planning_agent",
            "phase": "planning_done",
            "step": 4,
            "thought_summary": "must not be sent",
            "decision_trace": {"must": "not be sent"},
            "session_dialog": "must not be sent",
            "memory_keys_read": ["map:Pallet Town"],
            "action_plan": {
                "action": {"type": "move", "target": [7, 5]},
                "repeat_until": {"path": "position.x", "equals": 7},
                "max_repeats": 3,
            },
            "expected_result": "position.x == 7",
        }
    )

    event = hub.snapshot()["events"][-2]
    assert event["type"] == "PLAN_CREATED"
    assert event["payload"]["decision"] == {"type": "move", "target": [7, 5]}
    assert "thought_summary" not in event["payload"]
    assert "decision_trace" not in event["payload"]
    assert "session_dialog" not in event["payload"]

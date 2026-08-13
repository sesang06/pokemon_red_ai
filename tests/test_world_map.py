from pokemon_agent.memory.world_map import WorldMapTracker


def test_world_map_tracker_accumulates_observed_collision() -> None:
    tracker = WorldMapTracker()
    observation = {
        "state": {
            "map_id": 0,
            "map_name": "Pallet Town",
            "position": {"x": 10, "y": 6},
        },
        "game_area_collision": [[1 for _ in range(20)] for _ in range(18)],
    }

    summary = tracker.update_from_observation(observation)

    assert summary is not None
    assert summary["known_tiles"] == 90
    assert summary["visited_tiles"] == 1
    assert summary["nearest_screen_tile"] is not None

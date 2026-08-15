import { describe, expect, it } from "vitest";
import { reduceMessage } from "./live";
import type { Store } from "./live";
import type { LiveState } from "./types";

const state = {
  version: 1,
  updated_at: null,
  emulator: { status: "waiting", frame_index: 0, tool_step_index: 0, fps: 60, ticker_alive: false },
  game: { map_id: null, map_name: "Unknown", position: null, facing: null, mode: "unknown", dialog_open: false, dialog_text: null, in_battle: false, party: [], items: [], badges: [], money: null, screenshot: null, overlay: null },
  agent: { phase: "not_started", goal: { main: "Complete Pokemon Red", sub: "" }, action: null, result: null, pipeline: {}, current_step: 0, max_steps: null, planner_calls: 0, executor_actions: 0, interpreter_calls: 0, plan_error: null, interpret_error: null },
  navigation: { player: null, target: null, path: [], visible_cells: [], walk_area_collision: [], world_map: null },
  memory: { recent: [], last_activity: null },
  debug: { state_events: [], state_diff: null, action_outcome: null, ram: {}, screenshot_metadata: null },
} satisfies LiveState;

describe("reduceMessage", () => {
  it("applies state deltas without discarding unchanged nested values", () => {
    const initial = reduceMessage(
      { revision: 0, state: null, events: [] },
      { kind: "snapshot", revision: 1, state, events: [] },
    );
    const updated = reduceMessage(initial, {
      kind: "state_delta",
      revision: 2,
      changes: { game: { ...state.game, map_name: "Pallet Town" } },
    });

    expect(updated.state?.game.map_name).toBe("Pallet Town");
    expect(updated.state?.emulator.fps).toBe(60);
    expect(updated.revision).toBe(2);
  });

  it("keeps a bounded event history", () => {
    let store: Store = { revision: 1, state, events: [] };
    for (let id = 1; id <= 510; id += 1) {
      store = reduceMessage(store, {
        kind: "event",
        event: { id, timestamp: "2026-08-15T00:00:00Z", type: "STATE_CHANGED", source: "game", message: "state", payload: {} },
      });
    }
    expect(store.events).toHaveLength(500);
    expect(store.events[0].id).toBe(11);
  });
});

export type Point = { x: number; y: number };

export type ImagePayload = {
  format: string;
  width: number;
  height: number;
  base64: string;
};

export type PartyMember = {
  species: string;
  species_id: number | null;
  internal_species_id?: number | null;
  nickname: string | null;
  level: number | null;
  hp: number | null;
  max_hp: number | null;
  status: string | null;
  types: string[];
};

export type Item = { name: string; quantity: number; item_id: number | null };

export type LiveEvent = {
  id: number;
  timestamp: string;
  type: string;
  source: string;
  message: string;
  payload: Record<string, unknown>;
};

export type PipelineStatus = "idle" | "active" | "complete";

export type LiveState = {
  version: number;
  updated_at: string | null;
  emulator: {
    status: string;
    frame_index: number;
    tool_step_index: number;
    fps: number | null;
    snapshot_hz?: number | null;
    ticker_alive: boolean;
    ticker_error?: string | null;
  };
  game: {
    map_id: number | null;
    map_name: string;
    position: Point | null;
    facing: string | null;
    mode: string;
    dialog_open: boolean;
    dialog_text: string | null;
    in_battle: boolean;
    party: PartyMember[];
    items: Item[];
    badges: string[];
    money: number | null;
    game_time?: string | null;
    screenshot: ImagePayload | null;
    overlay: ImagePayload | null;
  };
  agent: {
    phase: string;
    objective: string | null;
    task: {
      id: string | null;
      description: string | null;
      status: string;
      attempt: number;
      step: number;
      max_steps: number | null;
      verification?: unknown;
    } | null;
    action: Record<string, unknown> | null;
    result: Record<string, unknown> | null;
    pipeline: Record<string, PipelineStatus>;
    planner_calls: number;
    executor_actions: number;
    interpreter_calls: number;
    plan_error: string | null;
    interpret_error: string | null;
    done?: boolean;
    termination_reason?: string | null;
  };
  navigation: {
    player: Point | null;
    target: Point | null;
    path: Point[];
    visible_cells: Array<Array<Point & { walkable: boolean }>>;
    walk_area_collision: number[][];
    world_map: Record<string, unknown> | null;
  };
  memory: {
    recent: Array<{ key: string; value: unknown; source: string | null; updated_at: string | null }>;
    last_activity: { type: string; keys: string[]; at: string } | null;
  };
  debug: {
    state_events: Array<Record<string, unknown>>;
    state_diff: Record<string, unknown> | null;
    action_outcome: Record<string, unknown> | null;
    ram: Record<string, unknown>;
    screenshot_metadata: Record<string, unknown> | null;
  };
};

export type LiveMessage =
  | { kind: "snapshot"; revision: number; state: LiveState; events: LiveEvent[] }
  | { kind: "state_delta"; revision: number; changes: Partial<LiveState> }
  | { kind: "event"; event: LiveEvent };

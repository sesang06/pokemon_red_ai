# Architecture

## Control Loop

```text
Goal
  -> Action Planner (Google ADK LLM, only when planning is required)
  -> bounded ActionPlan: action + optional repeat_until + max_repeats
  -> Python action validator/repeater
  -> buttons/move MCP action
  -> PokemonSession.observe() (RAM + vision)
  -> deterministic StateDiff and action/Goal verifier
       -> continue: reuse the same action without an LLM call
       -> condition met/limit/error/interruption: replan
  -> Result Interpreter (LLM only at an action-cycle boundary or durable event)
  -> Memory Consolidator
```

The optional web debugger receives the same live objects without polling PyBoy:

```text
PokemonSession cached observation -> latest-only listener -> LiveEventHub
ADK runtime/trace callbacks -------------------------------> LiveEventHub
LiveEventHub -> state deltas + typed events -> WebSocket -> React debugger
```

The session listener only replaces one pending observation. PNG serialization,
state/event normalization, and WebSocket fan-out run outside the emulator lock.
If the dashboard server or browser disconnects, the ticker and coordinator keep
running. Reconnecting starts with a current state snapshot plus at most 500
recent events.

The LLM chooses **one direct bounded action and repeat policy**. Python validates
and executes it, and structured game state decides **whether repetition should
stop and whether the Goal actually succeeded**.

## ADK Package Layout

```text
adk_agent/
  agents/
    planner/       # ADK planner, prompt, action/state contract
    executor/      # deterministic MCP executor and execution result contract
    interpreter/   # ADK result interpreter, prompt, memory contract
    shared.py      # shared streaming, JSON response, idle-pump, and trace helpers
  coordinator/
    loop.py        # Planning -> Execution -> Verification -> Interpretation
    action_cycle.py
  runtime/
    history.py
    logging.py
    session.py
    state.py
    trace.py
  web/
    app.py         # ADK Dev UI root/sub-agent definitions
    prompt.py
    tools.py
  client.py
  runner.py
dashboard/
  models.py       # LiveState serialization from real observations/runtime state
  events.py       # thread-safe state/event hub and bounded subscriber queues
  server.py       # optional FastAPI/WebSocket service and packaged frontend
  static/         # Vite production build included in the Python package
```

Role packages own their prompts and contracts. `coordinator` only orders the
roles, `runtime` owns persistence and diagnostics, and `web` contains the Dev UI
surface. The package root lazily exposes `app` and `root_agent` for ADK loading.

## Authority Order

```text
Actual RAM / GameState
> deterministic verifier
> active ActionPlan
> long-term memory
> LLM inference
```

Dialog text or a screenshot may help planning, but neither can prove that an
item, Pokemon, badge, or story reward was received.

## Runtime State

The coordinator keeps these state groups separate:

- `current_goal`: long-running objective and machine-verifiable conditions
- `active_action_plan`: direct action, optional RAM stop condition, repeat count/limit
- `planned_action`: the validated `buttons` or `move` action used this cycle
- `state_diff`: structured before/after changes and event types
- `action_outcome`: `continue`, `condition_met`, `single_action_complete`,
  `max_repeats_reached`, `interrupted`, or `execution_error`
- `transition_history`: recent structured state transitions
- `history_summary`: deterministic overflow summary

## Layer Responsibilities

### PyBoy and PokemonSession

`PyBoyEnv` owns a live PyBoy instance. `PokemonSession` exposes RAM-derived
state, screenshots, collision, world-coordinate navigation, realtime ticking,
and save states. A dedicated ticker thread owns runtime frame advancement;
actions only schedule input and wait for elapsed time or RAM state changes.
The ticker advances one frame per deadline. When the process is delayed it
drops the missed deadline instead of batching frames, preventing dialog and
battle animation jumps.

The same emulator thread applies queued buttons and performs runtime save/load
operations. It publishes RAM-derived state after every frame and refreshes the
screenshot, overlay, game area, and collision cache at 30 Hz. `observe()` only
copies the latest immutable snapshot, so planner and image-processing latency do
not hold the PyBoy lock or pause emulation. Qt pumping reads this cache and never
advances frames.

### Observation

`PokemonSession.observe()` is the observation source of truth. It returns:

- structured `GameState` and RAM values
- state events such as map/warp/dialog/battle/menu/item/party changes
- screenshot and collision overlay
- 20x18 game area/collision and internal 10x9 walk collision
- visible current-map world coordinates

### Action Planner

The Google ADK planner returns one direct action with an optional repeat policy:

```json
{
  "action":{"type":"buttons","buttons":["a","wait"],"reason":"advance_dialog"},
  "repeat_until":{"path":"dialog_open","equals":false},
  "max_repeats":8
}
```

Supported repeat operators are `equals`, `min`, `max`, and `contains`.
Unsupported condition shapes reject the whole model response and trigger one
rule-based fallback action. There are no planner-generated preconditions or
Task objects.

### Action Executor

The executor validates the planner's direct action and calls MCP/PokemonSession.
If `repeat_until` is still false, it reuses the same validated action on the
next cycle without calling the planner. Repetition stops at `max_repeats`, on
condition success, or on an execution interruption.

```json
{"type":"buttons","buttons":["a","wait"]}
{"type":"move","target":[9,3]}
```

Movement targets are current-map world coordinates. Collision conversion and
Dijkstra routing remain internal to the navigation/session layer.

The public action tools are intentionally small:

```text
press_buttons(buttons)
wait()
move_to_world_cell(target_x, target_y)
```

`press_buttons` accepts button names and the `wait` token. Each token is
serialized, and the call returns after the action has completed with a final
observation. Frame counts, button timing, path limits, nearest-target handling,
and walk-cell coordinates are private session details.

### StateDiff and Verifier

Every action compares before and after structured observations. The verifier
checks repeat and Goal conditions without an LLM. Supported evidence includes
inventory, party, Pokedex, badges, money, map, position, flags, dialog, battle,
warp, event types, and action results.

For example, `obtain_pokeballs` completes only when RAM-derived inventory
contains at least one Poke Ball. Opening or closing Oak dialog is insufficient.

### Result Interpreter and Memory

The interpreter is not a 20-turn history compressor. It is called only for an
action-cycle boundary that needs interpretation or a durable/unexpected event, and it may explain verified facts or
propose memory candidates. It cannot override deterministic outcomes.

Memory consolidation is a separate component. Long-term keys use:

```text
map:* event:* npc:* item:* goal:* strategy:* failure:* episode:*
```

Full actions remain in date-grouped JSONL and ADK SQLite. The model receives a
short state-transition context, while deterministic compression bounds recent
transition history to 20 entries.

## Process Boundary

The CLI currently uses `InProcessPokemonMcpClient`; ADK CLI and ADK Web still
own separate PyBoy processes. They share SQLite/runtime files for visibility,
not the live emulator object. A single remote MCP/PyBoy service remains a
separate deployment improvement.

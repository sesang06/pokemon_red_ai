# Architecture

## System Shape

```text
Planner LLM
    |
    v
Task Manager
    |
    +--> Navigation Agent --+
    +--> Battle Agent -----+--> Action Executor --> PyBoy
    +--> Dialog Agent -----+
    +--> Inventory Agent --+
                              |
                              v
                 Memory Reader + Screen/Tile Reader
                              |
                              v
                        World State
                              |
                              v
                    Long-Term Memory Store
```

## Layer Responsibilities

### PyBoy Environment

`PyBoyEnvironment` is the only module that owns a live PyBoy instance. It wraps
frame ticking, button input, RAM access, screen buffers, tile maps, game wrapper
access, and save-state IO.

### World State

`GameState` is the contract consumed by planners and agents. It should be small,
stable, and easy to log:

```text
map_id
map_name
position
facing
mode
in_battle
dialog_open
money
party
items
nearby_npcs
nearby_exits
flags
raw
```

The LLM never sees pixels as its primary input. It receives a summary derived
from this state.

### Memory Reader

`PokemonRedMemoryReader` reads known RAM addresses and builds `GameState`.
Addresses are centralized in `PokemonRedRamMap` so that Red/Blue/Yellow
variants can be swapped later.

The current defaults seed only stable early fields:

- current map id
- player x/y
- collision pointer
- grass tile
- tileset type

Party, inventory, flags, and battle parsing should be added incrementally with
tests against save states.

### Vision and Tile Parsing

Vision is a support layer. For Pokemon Red, tile maps and sprites are usually
more reliable than image OCR. The screen reader exposes:

- RGB frame copies for logging or optional computer vision
- background/window tile maps
- PyBoy Pokemon Gen1 collision grid when available

### Planner

The planner converts `GameState` into a high-level `Goal`. The initial
implementation is a scripted planner with a replaceable `Planner` interface.
An LLM planner can be added behind the same interface once state summaries are
stable.

### Task Manager

The task manager is the state machine. It chooses which specialized agent gets
control on each tick:

- battle state -> battle agent
- dialog/menu state -> dialog agent
- active target position -> navigation agent
- no active task -> planner

### Navigation

Navigation does not need an LLM. It uses A* over a walkability grid and turns
the route into direction button actions. This keeps movement reproducible and
easy to test.

### Battle

The starter battle agent is intentionally conservative. It can be expanded from
menu heuristics into type-aware rule logic, then into a learned policy for this
module only.

### Dialog

The starter dialog agent advances text with `A`. Later it should OCR text boxes
or parse text buffers from RAM and emit quest flag updates.

### Save State Manager

Save states are checkpoints. Use them before risky transitions such as trainer
battles, caves, long grass routes, and major menu sequences.

## Milestones

1. Boot ROM and produce logged `GameState` frames.
2. Start from a save state in Pallet Town and move to a target coordinate.
3. Detect dialog and advance it safely.
4. Detect battle mode and repeatedly choose valid fight actions.
5. Parse local collision and use A* within one map.
6. Load static map metadata from `pret/pokered`-derived data.
7. Add a route graph for the main story.
8. Add LLM planning for subgoal selection and stuck recovery.
9. Add persistent memory for visited maps, talked NPCs, and failed routes.
10. Add retry policies around save states.

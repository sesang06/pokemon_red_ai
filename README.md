# Pokemon Red AI

An experimental Pokemon Red agent runtime built with PyBoy, Google ADK, MCP,
and a live React dashboard. The agent observes RAM and game images, plans a
bounded action, executes it through deterministic controls, and interprets the
result before the next cycle.

> This repository does not include a Pokemon Red ROM, save state, or copyrighted
> game assets. Use a legally obtained ROM and keep it local.

## Features

- Real-time Pokemon Red emulation through PyBoy
- RAM-derived map, position, facing, dialog, battle, party, inventory, and event state
- Color screenshots plus collision and world-coordinate overlays
- Collision-aware Dijkstra navigation using current-map world coordinates
- Automatic screen-by-screen replanning for destinations outside the current view
- Google ADK Planner -> deterministic Executor -> Result Interpreter workflow
- Gemini vision input for the latest screenshot and overlay
- File-backed long-term memory for maps, NPCs, Pokemon, and events
- Volatile main/sub goal state restored across runs
- Save/load state controls and manual PySide6 UI
- Local MCP server and ADK Dev UI integration
- FastAPI/WebSocket dashboard at `http://127.0.0.1:8765`

## Architecture

```text
Latest RAM + screenshot + collision overlay + goal
                         |
                         v
                 Planning Agent (LLM)
                         |
             buttons or world-coordinate move
                         |
                         v
              Deterministic Executor
                         |
              PyBoy / PokemonSession / MCP
                         |
                         v
          Fresh observation and verified state diff
                         |
                         v
              Result Interpreter (LLM)
                 |                   |
             update goal       search/save memory
                         |
                         v
                    next cycle
```

PyBoy runs on a dedicated real-time ticker. LLM latency does not advance frames
in batches or block cached observations. Navigation plans one visible segment at
a time, waits for real movement, refreshes collision data, and replans toward the
same current-map world coordinate.

See [docs/architecture.md](docs/architecture.md) for implementation details and
[docs/realtime-dashboard.md](docs/realtime-dashboard.md) for the dashboard event
contract.

## Requirements

- Windows, macOS, or Linux
- Python 3.11+
- A legally obtained English Pokemon Red ROM
- Google Gemini API key for ADK planning
- Node.js and pnpm only when rebuilding the dashboard frontend
- ffmpeg only for optional MP4 capture

## Installation

Clone the repository and create one virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Alternatively, with `uv`:

```powershell
uv sync --extra dev
```

Place the ROM at:

```text
src/pokered.gb
```

The first run creates `states/fixed_start.state` when it does not exist. ROMs,
states, captures, logs, API keys, SQLite sessions, and long-term memory are all
ignored by Git.

For Gemini, copy `.env.example` to `.env` and set:

```dotenv
GOOGLE_API_KEY=your-key
POKEMON_AGENT_ADK_MODEL=gemini-3.5-flash
```

## Manual Play

Launch Pokemon Red with the PySide6 control panel:

```powershell
.\.venv\Scripts\pokemon-play.exe
```

or:

```powershell
.\.venv\Scripts\python.exe run_pokemon_play.py
```

The panel provides save/load controls, button-array input, RAM inspection,
screenshots, and a collision/world-coordinate overlay. A move target is a
current-map world coordinate. It may be outside the current screen; the runtime
moves to the best visible frontier, refreshes the screen, and continues planning
until it reaches the destination or encounters an interruption.

Button arrays accept:

```json
["a", "wait", "right", "right", "start"]
```

Useful manual-play options:

```powershell
# Save the final emulator state to states/last.state
.\.venv\Scripts\pokemon-play.exe --save-final

# Overwrite states/fixed_start.state when stopping
.\.venv\Scripts\pokemon-play.exe --set-fixed

# Run without the control panel
.\.venv\Scripts\pokemon-play.exe --no-control-ui
```

## ADK Autoplay

Start the vision-enabled three-agent loop with its live dashboard:

```powershell
.\.venv\Scripts\pokemon-adk.exe
```

Current defaults include:

- up to 10,000 action cycles
- Gemini planning and result interpretation
- medium thinking level without private thinking output
- latest screenshot and collision overlay as transient vision input
- continuous 60 FPS emulation and 30 Hz visual snapshots
- PySide6 control UI and audio
- dashboard at [http://127.0.0.1:8765](http://127.0.0.1:8765)

Override the restored main goal when starting a run:

```powershell
.\.venv\Scripts\pokemon-adk.exe --main-goal "Complete Pokemon Red"
```

Run headless without vision or dashboard:

```powershell
.\.venv\Scripts\pokemon-adk.exe --window null --no-control-ui --no-adk-vision --no-dashboard
```

The Planner returns exactly one direct action:

```json
{"type":"buttons","buttons":["a","wait","a"],"reason":"advance_dialog"}
```

or:

```json
{
  "type":"move",
  "waypoints":[[9,5],[16,5]],
  "target":[24,8],
  "reason":"follow_the_route_exit"
}
```

`target` and `waypoints` are current-map world coordinates, not screen tiles or
the internal 10x9 walk grid. A map transition intentionally interrupts the
current move because the coordinate system changes with `map_id`.

## Live Dashboard

The packaged dashboard shows:

- current game screen and collision/world-coordinate overlay
- RAM-derived state, mode, position, facing, dialog, and battle information
- current main/sub goal and action result
- party information and Pokemon sprites
- Planner and Result Interpreter summaries
- recently accessed long-term memory
- current and maximum agent step counts

To rebuild the frontend:

```powershell
cd dashboard\frontend
pnpm install
pnpm build
```

The production bundle is written to
`src/pokemon_agent/dashboard/static` and included in the Python package.

## ADK Dev UI

Start the Google ADK development interface:

```powershell
.\run_adk_web.ps1
```

Open [http://127.0.0.1:8000/dev-ui/](http://127.0.0.1:8000/dev-ui/) and select
the `adk_agent` app. A prompt such as `Play Pokemon for 100 steps.` invokes the
same native Planner -> Executor -> Result Interpreter team used by the CLI.
Sub-agent model calls, tool calls, token metadata, and phase events are visible
in the ADK trace.

Raw ADK events are stored locally in `data/adk_sessions.db`. Context compaction
bounds model input while retaining raw events for diagnostics. Historical image
payloads are filtered so only the latest transient screenshot and overlay are
sent to each vision call.

## MCP Server

Run the local stdio MCP server:

```powershell
.\.venv\Scripts\pokemon-mcp.exe
```

Headless mode:

```powershell
.\.venv\Scripts\pokemon-mcp.exe --no-auto-start --no-control-ui
```

Primary tools:

- `start_session`
- `observe`
- `press_buttons`
- `wait`
- `move_to_world_cell`
- `save_state`
- `load_state`
- `reset_to_fixed`
- `set_realtime_ticks`
- `realtime_tick_status`
- `recent_mcp_commands`

`observe()` returns structured RAM state, game/collision matrices, visible world
coordinates, a PNG screenshot, and a PNG collision overlay. Public actions do
not expose frame timing or walk-cell coordinates.

## Memory and Runtime Data

Generated local data is intentionally not committed:

```text
data/long_term_memory.json   map, NPC, Pokemon, and event memories
data/adk_runtime_state.json  latest goal and runtime snapshot
data/adk_sessions.db         ADK event/session trace
logs/actions/YYYYMMDD/       date-grouped action JSONL
states/                      PyBoy save states
captures/                    screenshots and recordings
```

Long-term memory tools support batched search and atomic append/replace writes.
The latest goal contains only:

```json
{
  "goal": {
    "main": "Complete Pokemon Red",
    "sub": "Reach the next concrete story milestone"
  }
}
```

## Development

Run the full Python test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run dashboard tests and build:

```powershell
cd dashboard\frontend
pnpm test
pnpm build
```

Project layout:

```text
src/pokemon_agent/
  adk_agent/
    agents/          Planner, Executor, Result Interpreter, memory/goal tools
    coordinator/     ordered action cycle and native ADK workflow
    runtime/         state, history, persistence, logging, trace
    web/             ADK Dev UI app and tools
  dashboard/         FastAPI/WebSocket server and packaged frontend
  emulator/          PyBoy adapter
  memory/            RAM reader, GameState, world map, file memory
  tools/             Dijkstra and shared screen/world navigation
  ui/                PySide6 control panel
  vision/            screenshots, collision overlays, capture
  mcp_server.py      local MCP interface
  session.py         real-time PyBoy session owner
dashboard/frontend/  React dashboard source
tests/               unit and integration tests
```

## Limitations

- Navigation coordinates are local to the current map.
- Story planning quality depends on the selected Gemini model and available context.
- RAM addresses and dialog decoding target English Pokemon Red.
- Save states are emulator/version-sensitive and are not portable project assets.
- This project is for research and personal experimentation; it is not affiliated
  with Nintendo, Game Freak, Creatures, or The Pokemon Company.

## References

- [PyBoy documentation](https://docs.pyboy.dk/)
- [Google ADK documentation](https://google.github.io/adk-docs/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [pret/pokered](https://github.com/pret/pokered)

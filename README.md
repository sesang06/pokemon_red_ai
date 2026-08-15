# Pokemon Red Agent

This repo is a concrete scaffold for a hierarchical Pokemon Red agent built on
PyBoy. The design follows the attached plan:

Planner LLM -> bounded one-shot ActionPlan -> deterministic verification loop ->
PyBoy -> Memory/Screen readers -> World State -> Memory DB.

The first milestone is not end-to-end game completion. It is a reliable control
loop that can:

- read Pokemon Red RAM into a stable `GameState`
- use screen/tile APIs for local walkability
- route movement through A*
- isolate battle, dialog, inventory, and planning decisions
- save and restore emulator state around risky actions
- keep long-term observations in a replaceable memory store

ROM files and copyrighted map data are intentionally not included.

## Quick Start

Run the fixed Pokemon Red launcher. This always uses `src\pokered.gb` and
starts from `states\fixed_start.state`. It also opens a PySide6 control panel
with buttons for `Save Fixed`, `Save Snapshot`, `Load Fixed`, `Move`, and `Quit`.
The `Move` inputs use current map coordinates from the overlay/status; the code
converts them to the visible 10x9 walk grid internally:
The `Buttons` input accepts arrays such as `["a","wait"]` or comma-separated
text like `a, wait, b`, then sends one token at a time with a delay between
tokens.

```powershell
python run_fixed_pokered.py
```

The control panel updates once per second and has tabs for:

- `RAM Map`: selected Data Crystal RAM-map fields read with `pyboy.memory[0xADDR]`
- `Game Area`: live `pyboy.game_area()` matrix
- `Collision`: live `pyboy.game_area_collision()` matrix

The structured RAM reader also decodes player/rival names, badges, money,
coins, game time, tileset, party Pokemon details, moves, PP, status conditions,
inventory item names, map warps, dialog text, and Pokedex caught count. The
detail level follows the same style as cicero225's LLM Pokemon scaffold memory
reader while keeping unknown values non-fatal.

To run with the project's `.venv` interpreter directly:

```powershell
.\run_fixed_pokered_qt.ps1
```

or:

```powershell
.\run_fixed_pokered_qt.cmd
```

To run without the control panel:

```powershell
python run_fixed_pokered.py --no-control-ui
```

To play manually and overwrite the fixed starting state when you stop, run:

```powershell
python run_fixed_pokered.py --set-fixed
```

When you reach the desired point, return to the terminal and press `Ctrl+C`.
The program will call PyBoy `save_state` and overwrite
`states\fixed_start.state`.

If a PyBoy hotkey save (`src\pokered.gb.state`) exists, you can import it with:

```powershell
python run_fixed_pokered.py --fix-current
```

To reset the fixed state back to the ROM boot state:

```powershell
python run_fixed_pokered.py --boot-state
```

Run against a legally obtained Pokemon Red ROM:

```powershell
python -m pokemon_agent.app --rom "C:\path\to\Pokemon Red.gb" --steps 500
```

Open a live emulator window while the agent runs:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --window SDL2 --render
```

PyBoy's SDL2 window also has built-in save-state hotkeys:

- `Z`: save to `ROM.gb.state`
- `X`: load from `ROM.gb.state`

Run the emulator for 36,000 loop steps:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --window SDL2 --render --steps 36000
```

Autosave every 600 loop steps:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --window SDL2 --render --steps 36000 --save-every 600
```

Load from a previous PyBoy state and save again on exit:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --window SDL2 --render --load-state .\src\pokered.gb.state --save-final states\last.state
```

Save progress screenshots and a short GIF:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --steps 300 --screenshot-every 30 --record-gif captures\run.gif --video-every 2 --video-fps 15
```

Save an MP4 if `ffmpeg` is installed:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --steps 300 --record-mp4 captures\run.mp4 --video-every 2 --video-fps 30
```

If PyBoy prints warnings about missing `Pillow`, install/sync dependencies again:

```powershell
uv sync --extra dev
```

or:

```powershell
python -m pip install -e ".[dev]"
```

Install for local development:

```powershell
python -m pip install -e ".[dev]"
pytest
```

## Real-time Web Debugger

`pokemon-adk` serves a connected Pokemon Red runtime debugger at
`http://127.0.0.1:8765` by default. The page displays the actual PyBoy frame,
RAM-derived game state, current goal/action/result, visible world cells and
path, party, inventory, agent pipeline, recent memory, and a bounded structured
event log. Gemini's official thinking summaries are streamed into the
`THINKING SUMMARY` inspector and recorded once per completed model call in the
event log. A summary can remain empty when Gemini does not return one. The page
is an observer only; closing or reloading it does not pause PyBoy or the agent.

```powershell
.\.venv\Scripts\pokemon-adk.exe
```

Use `--no-dashboard` to disable the web server, or `--dashboard-host` and
`--dashboard-port` to change its bind address. The production frontend is
packaged with Python. To modify it locally:

```powershell
cd dashboard\frontend
pnpm install
pnpm dev
```

`pnpm dev` proxies `/api` and `/ws` to port 8765. Run `pnpm build` after a
frontend change to regenerate `src\pokemon_agent\dashboard\static`.

Party sprites are generated directly from the RAM-derived National Pokédex
`species_id`; no REST lookup occurs on render. They use the pixel-art
[PokéAPI Generation I Red/Blue sprite assets](https://github.com/PokeAPI/sprites/tree/master/sprites/pokemon/versions/generation-i/red-blue/transparent).
Pokemon Red's internal party index is preserved separately as
`internal_species_id` and normalized with the
[pret/pokered Pokédex order table](https://github.com/pret/pokered/blob/master/data/pokemon/dex_order.asm).

See [docs/realtime-dashboard.md](docs/realtime-dashboard.md) for the transport,
event contract, performance behavior, and verification commands.

## MCP + Google ADK

Run the Pokemon Red MCP server over local stdio. By default this starts the
fixed ROM session and opens the PySide6 control panel, so the RAM Map, Game
Area, Collision, World Map, MCP Log tabs, and live game-screen preview update
whenever MCP tools observe or advance the game. The MCP Log tab shows each
received tool call, its arguments, and the final `ok` or `error` status. The
stdio server also keeps the Qt event loop
pumped, so the panel remains responsive while the MCP server is running:

```powershell
.\.venv\Scripts\python.exe -m pokemon_agent.mcp_server
```

To also print MCP command logs in the terminal, run with `--log-level INFO`:

```powershell
.\.venv\Scripts\python.exe -m pokemon_agent.mcp_server --log-level INFO
```

To keep the game ticking and the control-panel screen updating even when no
planner is sending actions, enable realtime ticks:

```powershell
.\.venv\Scripts\python.exe -m pokemon_agent.mcp_server --realtime-ticks --realtime-fps 60 --ui-refresh-hz 30
```

For a pure headless MCP server:

```powershell
.\.venv\Scripts\python.exe -m pokemon_agent.mcp_server --no-auto-start --no-control-ui
```

Register it with an MCP client using this shape:

```json
{
  "mcpServers": {
    "pokemon-red-pyboy": {
      "command": "C:\\Users\\sesan\\Documents\\New project 2\\.venv\\Scripts\\python.exe",
      "args": ["-m", "pokemon_agent.mcp_server"],
      "cwd": "C:\\Users\\sesan\\Documents\\New project 2"
    }
  }
}
```

Important MCP tools:

- `start_session(window="null", load_fixed=true, control_ui=true)`
- `observe()`: returns RAM, `game_area`, `game_area_collision`, `visible_world_cells`, and PNG screenshot base64
- `press_buttons(buttons)`: executes button and `wait` tokens through the realtime ticker
- `wait()`: waits 300 milliseconds while the realtime ticker advances the game
- `move_to_world_cell(target_x, target_y)`: collision-aware movement by current map/world coordinates
- `save_state(kind="snapshot" | "fixed" | "last")`
- `load_state(kind="fixed" | "last")`
- `reset_to_fixed()`
- `recent_mcp_commands(limit=50)`: returns the same recent command log shown in the control panel
- `set_realtime_ticks(enabled=true, fps=60)`: keeps PyBoy ticking outside planner actions
- `realtime_tick_status()`

Starting a session starts a fixed-step realtime ticker immediately. The ticker
advances exactly one frame per scheduled interval and drops missed wall-clock
deadlines instead of batching catch-up frames. It also owns all runtime PyBoy
button, save, and load calls. `observe()` returns the latest cached RAM and
visual snapshot, so LLM latency and screenshot consumers do not pause the game.
RAM-derived state is refreshed every frame; screenshots and overlays refresh at
30 Hz. `pump_realtime()` only refreshes Qt/UI state and reports progress.

The session converts the visible collision data internally and routes with
Dijkstra before scheduling D-pad buttons. Agents and ADK Web use only
`move_to_world_cell` or `{"type":"move","target":[x,y]}` with current
map/world coordinates.

The Python executor retains two action schemas:

```json
{"type":"buttons","buttons":["a","wait"]}
{"type":"move","target":[1,3]}
```

For the executor, `move.target` is always a current map/world coordinate, matching
the collision overlay labels and `state.position`. One `move` action is bounded
to at most 8 Dijkstra path steps.

The Google ADK planner returns one of those actions directly. Every plan is
executed once, followed by a fresh RAM/GameState observation and a new planner
call. Repeated button input is written explicitly in one ordered button array.

```json
{
  "action":{"type":"buttons","buttons":["a","wait","a","wait","a"],"reason":"advance_dialog_three_times"}
}
```

Run the Google ADK-backed safe loop:

```powershell
.\.venv\Scripts\pokemon-adk.exe
```

By default, this uses `--steps 10000 --window SDL2 --control-ui
--realtime-ticks --realtime-fps 60 --ui-refresh-hz 30 --adk-model
"gemini-3.5-flash" --adk-vision`. With the control UI enabled, the game and
collision overlay are rendered in one Qt window while a windowless SDL audio
device plays BGM and sound effects. Use `--window null` for silent headless execution.

Run with a Google ADK model planner:

```powershell
$env:GOOGLE_API_KEY="your-api-key"
.\.venv\Scripts\python.exe -m pokemon_agent.adk_agent.runner --steps 20 --window SDL2 --control-ui --adk-model "gemini-3.5-flash"
```

You can also create a local `.env` file. It is ignored by git:

```powershell
$env:GOOGLE_API_KEY="your-api-key"
Set-Content .env "GOOGLE_API_KEY=$env:GOOGLE_API_KEY"
Add-Content .env "POKEMON_AGENT_ADK_MODEL=gemini-3.5-flash"
```

Then run:

```powershell
$env:POKEMON_AGENT_ADK_MODEL="gemini-3.5-flash"
.\.venv\Scripts\python.exe -m pokemon_agent.adk_agent.runner
```

The default ADK runner sends the latest screenshot and collision overlay to the
planner. The model returns direct `buttons` or world-coordinate `move` JSON with
an explicit one-shot input sequence. Invalid or unavailable LLM responses stop
the loop with `planning_failed` without sending game input. Use
`--no-adk-vision` to disable image input.

Each ADK execution action is also written as JSONL grouped by local date:
`logs\actions\YYYYMMDD\actions.jsonl`. Use `--action-log-dir <path>` to change
the directory or `--no-action-log` to disable this file log.

The CLI also writes all ADK planner/interpreter events to the shared SQLite DB
`data\adk_sessions.db`. Model context remains bounded: every five completed
planner turns are compacted with one turn of overlap, while the result interpreter is stateless. Planner
calls occur once per executed action.
Interpreter calls occur only at failures, Goal completion,
or durable events; a 20-entry history limit does not
trigger LLM calls. The complete DB trace remains available to ADK Dev UI.
Structured `current_goal`, `active_action_plan`, `action_outcome`, `state_diff`, and
`action_history` are updated atomically in `data\adk_runtime_state.json` for Dev UI status tools.

For live UI refresh, realtime ticking, LLM planning, and vision together:

```powershell
.\.venv\Scripts\pokemon-adk.exe
```

Run the ADK Web UI with Pokemon game tools:

```powershell
.\run_adk_web.ps1
```

This starts Dev UI with the same `data\adk_sessions.db` used by
`pokemon-adk.exe`. Open `http://localhost:8000`, select the `adk_agent` app and
user `user`. The CLI sessions are
`pokemon-red-planner` and
`pokemon-red-result-interpreter`; refresh the session list/event view while the
CLI runs to load newly persisted model events.

The root agent also exposes `agent_runtime_status` and `recent_agent_actions`. In Dev UI, try:

```text
Show the separately running CLI agent status and recent actions.
```

ADK Web will show tool calls such as `start_game`, `observe_game`,
`save_current_screenshot`, `move`, `buttons`, and `recent_game_commands` in the
event history. Screenshot base64 is omitted from
tool results by default; ask for `save_current_screenshot()` to write a PNG to
`captures\YYYYMMDD\adk_web_HHMMSS_mmm.png`, or ask for
`observe_game(include_screenshot_base64=true)` when you need to inspect the raw
image payload.

## Design Notes

- The Google ADK planner produces one bounded `buttons` action or one persistent current-map `move` ActionPlan.
- Python validates each action once and owns collision checks, screen-by-screen Dijkstra replanning, and interruption handling.
- RAM-derived GameState deterministically verifies action results and Goal success.
- The Planner reads relevant map, NPC, Pokemon, and event memories through
  `search_memory(memory_type, name)`.
- The result interpreter uses `search_memory` and `save_memory`; persisted keys are generated internally as
  `map:<name>`, `npc:<name>`, `pokemon:<name>`, or `event:<name>`.
- Navigation is deterministic Dijkstra over the latest visible walkability grid and automatically replans toward remote current-map coordinates.
- Battle and dialog are isolated because they use different observations and
  menu timing than overworld movement.
- RAM reads are the source of truth for state such as map id and coordinates.
- Screen/tile reads are supporting evidence for local terrain and UI state.
- Save states are explicit checkpoints for retries.
- After every completed agent turn, the current emulator state overwrites `states\fixed_start.state`; the periodic
  `--checkpoint-every` backup to `states\last.state` remains separate.

Useful upstream references:

- PyBoy API: https://docs.pyboy.dk/
- PyBoy Pokemon Gen1 wrapper: https://docs.pyboy.dk/plugins/game_wrapper_pokemon_gen1.html
- pret/pokered disassembly: https://github.com/pret/pokered

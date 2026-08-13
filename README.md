# Pokemon Red Agent

This repo is a concrete scaffold for a hierarchical Pokemon Red agent built on
PyBoy. The design follows the attached plan:

Planner LLM -> Task Manager -> Navigation/Battle/Dialog agents -> Action
Executor -> PyBoy -> Memory/Screen readers -> World State -> Memory DB.

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

Dry-run the architecture without a ROM:

```powershell
python -m pokemon_agent.app --dry-run
```

Run the fixed Pokemon Red launcher. This always uses `src\pokered.gb` and
starts from `states\fixed_start.state`. It also opens a PySide6 control panel
with buttons for `Save Fixed`, `Save Snapshot`, `Load Fixed`, and `Quit`:

```powershell
python run_fixed_pokered.py
```

The control panel updates once per second and has tabs for:

- `RAM Map`: selected Data Crystal RAM-map fields read with `pyboy.memory[0xADDR]`
- `Game Area`: live `pyboy.game_area()` matrix
- `Collision`: live `pyboy.game_area_collision()` matrix

If your active `.venv` is broken or does not have PySide6, use the installed Qt
environment directly:

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

For manual play with this app keeping the emulator ticking:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --window SDL2 --render --manual-play --steps 36000
```

Autosave every 600 loop steps:

```powershell
python -m pokemon_agent.app --rom .\src\pokered.gb --window SDL2 --render --manual-play --steps 36000 --save-every 600
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
uv sync --extra dev --extra vision --extra memory
```

or:

```powershell
python -m pip install -e ".[dev,vision,memory]"
```

Install for local development:

```powershell
python -m pip install -e ".[dev,vision,memory]"
pytest
```

## MCP + Google ADK

Run the Pokemon Red MCP server over local stdio. By default this starts the
fixed ROM session and opens the PySide6 control panel, so the RAM Map, Game
Area, Collision, World Map, MCP Log tabs, and live game-screen preview update
whenever MCP tools observe or advance the game. The MCP Log tab shows each
received tool call, its arguments, and the final `ok` or `error` status. The
stdio server also keeps the Qt event loop
pumped, so the panel remains responsive while the MCP server is running:

```powershell
.\.venv_qt\Scripts\python.exe -m pokemon_agent.mcp_server
```

To also print MCP command logs in the terminal, run with `--log-level INFO`:

```powershell
.\.venv_qt\Scripts\python.exe -m pokemon_agent.mcp_server --log-level INFO
```

To keep the game ticking and the control-panel screen updating even when no
planner is sending actions, enable realtime ticks:

```powershell
.\.venv_qt\Scripts\python.exe -m pokemon_agent.mcp_server --realtime-ticks --realtime-fps 60 --ui-refresh-hz 30
```

For a pure headless MCP server:

```powershell
.\.venv_qt\Scripts\python.exe -m pokemon_agent.mcp_server --no-auto-start --no-control-ui
```

Register it with an MCP client using this shape:

```json
{
  "mcpServers": {
    "pokemon-red-pyboy": {
      "command": "C:\\Users\\sesan\\Documents\\New project 2\\.venv_qt\\Scripts\\python.exe",
      "args": ["-m", "pokemon_agent.mcp_server"],
      "cwd": "C:\\Users\\sesan\\Documents\\New project 2"
    }
  }
}
```

Important MCP tools:

- `start_session(window="null", load_fixed=true, control_ui=true)`
- `observe()`: returns RAM, `game_area`, `game_area_collision`, and PNG screenshot base64
- `press_button(button, frames=4, after_frames=8)`
- `execute_actions(actions)`
- `move_to_screen_tile(target_x, target_y, max_steps=8, accept_nearest=true)`
- `save_state(kind="snapshot" | "fixed" | "last")`
- `load_state(kind="fixed" | "last")`
- `reset_to_fixed()`
- `recent_mcp_commands(limit=50)`: returns the same recent command log shown in the control panel
- `set_realtime_ticks(enabled=true, fps=60)`: keeps PyBoy ticking outside planner actions
- `realtime_tick_status()`

`move_to_screen_tile` uses `game_area` tile coordinates, where `(0, 0)` is the
top-left screen tile. It compresses the 20x18 collision matrix into a 10x9 walk
grid and routes with Dijkstra before pressing D-pad buttons.

Run the Google ADK-backed safe loop:

```powershell
.\.venv_qt\Scripts\pokemon-adk.exe
```

By default, this uses `--steps 100 --window null --control-ui
--realtime-ticks --realtime-fps 60 --ui-refresh-hz 30 --adk-model
"gemini-2.5-flash" --adk-vision`. Use `--window SDL2` if you also want to see
the emulator window.

Run with a Google ADK model planner:

```powershell
$env:GOOGLE_API_KEY="your-api-key"
.\.venv_qt\Scripts\python.exe -m pokemon_agent.adk_agent.runner --steps 20 --window null --control-ui --adk-model "gemini-2.5-flash"
```

You can also create a local `.env` file. It is ignored by git:

```powershell
$env:GOOGLE_API_KEY="your-api-key"
Set-Content .env "GOOGLE_API_KEY=$env:GOOGLE_API_KEY"
Add-Content .env "POKEMON_AGENT_ADK_MODEL=gemini-2.5-flash"
```

Then run:

```powershell
$env:POKEMON_AGENT_ADK_MODEL="gemini-2.5-flash"
.\.venv_qt\Scripts\python.exe -m pokemon_agent.adk_agent.runner
```

The default ADK runner sends the current screenshot to the ADK model. The model
can only return bounded JSON actions. Invalid or unsafe responses fall back to
the built-in rule planner. Use `--no-adk-vision` to disable screenshot input or
`--no-adk-model` to use the built-in rule planner only.

For live UI refresh, realtime ticking, LLM planning, and vision together:

```powershell
.\.venv_qt\Scripts\pokemon-adk.exe
```

Run the ADK Web UI with Pokemon game tools:

```powershell
.\.venv_qt\Scripts\adk.exe web --port 8000 --no-reload src\pokemon_agent\adk_agent
```

Then open `http://localhost:8000`, select `pokemon_red_planner`, and try:

```text
Start the game, observe it, then run one safe rule-based step.
```

ADK Web will show tool calls such as `start_game`, `observe_game`,
`save_current_screenshot`, `move_to_screen_tile`, `press_button`, `run_rule_based_step`, and
`recent_game_commands` in the event history. Screenshot base64 is omitted from
tool results by default; ask for `save_current_screenshot()` to write a PNG to
`captures\YYYYMMDD\adk_web_HHMMSS_mmm.png`, or ask for
`observe_game(include_screenshot_base64=true)` when you need to inspect the raw
image payload.

## Design Notes

- The Google ADK planner produces bounded action JSON, not raw unlimited button presses.
- The task manager owns the state machine and routes work to specialized agents.
- Navigation is deterministic A* over a walkability grid.
- Battle and dialog are isolated because they use different observations and
  menu timing than overworld movement.
- RAM reads are the source of truth for state such as map id and coordinates.
- Screen/tile reads are supporting evidence for local terrain and UI state.
- Save states are explicit checkpoints for retries.

Useful upstream references:

- PyBoy API: https://docs.pyboy.dk/
- PyBoy Pokemon Gen1 wrapper: https://docs.pyboy.dk/plugins/game_wrapper_pokemon_gen1.html
- pret/pokered disassembly: https://github.com/pret/pokered

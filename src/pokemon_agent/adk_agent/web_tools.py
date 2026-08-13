from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pokemon_agent.adk_agent.client import InProcessPokemonMcpClient
from pokemon_agent.adk_agent.loop import PokemonAdkLoop


def start_game(
    window: str = "null",
    load_fixed: bool = True,
    control_ui: bool = True,
    realtime_ticks: bool = True,
    realtime_fps: float = 60.0,
) -> dict[str, Any]:
    """Start the Pokemon Red PyBoy session for ADK Web tool testing."""

    server = _mcp_server()
    start_result = server.start_session(window=window, load_fixed=load_fixed, control_ui=control_ui)
    realtime_result = None
    if realtime_ticks:
        realtime_result = server.set_realtime_ticks(enabled=True, fps=realtime_fps)

    return {
        "session": start_result,
        "realtime": realtime_result,
        "next": "Call observe_game, move_to_screen_tile, press_button, or run_rule_based_step.",
    }


def stop_game(save_final: bool = False) -> dict[str, Any]:
    """Stop the active Pokemon Red PyBoy session."""

    return _mcp_server().stop_session(save_final=save_final)


def observe_game(include_screenshot_base64: bool = False) -> dict[str, Any]:
    """Observe the current game state, RAM-derived state, screen tiles, collision, and screenshot metadata."""

    _ensure_game_started()
    observation = _mcp_server().observe()
    return compact_observation(observation, include_screenshot_base64=include_screenshot_base64)


def save_current_screenshot(
    filename: str = "",
    include_base64: bool = False,
) -> dict[str, Any]:
    """Save the current game screenshot to captures/YYYYMMDD/ and return the absolute PNG path."""

    _ensure_game_started()
    observation = _mcp_server().observe()
    screenshot = observation.get("screenshot", {})
    screenshot_base64 = screenshot.get("base64")
    if not screenshot_base64:
        raise RuntimeError("Current observation does not include a screenshot.")

    path = _resolve_capture_path(filename)
    png_bytes = base64.b64decode(screenshot_base64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)

    result = {
        "saved": True,
        "path": str(path),
        "filename": path.name,
        "bytes": len(png_bytes),
        "screenshot": {
            "format": screenshot.get("format"),
            "width": screenshot.get("width"),
            "height": screenshot.get("height"),
            "base64_length": len(str(screenshot_base64)),
        },
        "state": _compact_state(observation.get("state", {})),
    }
    if include_base64:
        result["screenshot"]["base64"] = screenshot_base64
        result["screenshot"]["data_uri"] = f"data:image/png;base64,{screenshot_base64}"
    return result


def press_button(button: str, frames: int = 4, after_frames: int = 8) -> dict[str, Any]:
    """Press one Game Boy button and return a compact result."""

    _ensure_game_started()
    result = _mcp_server().press_button(button=button, frames=frames, after_frames=after_frames)
    return compact_tool_result(result)


def move_to_screen_tile(
    target_x: int,
    target_y: int,
    max_steps: int = 1,
    accept_nearest: bool = True,
) -> dict[str, Any]:
    """Move toward a current-screen tile using collision-aware routing."""

    _ensure_game_started()
    result = _mcp_server().move_to_screen_tile(
        target_x=target_x,
        target_y=target_y,
        max_steps=max_steps,
        accept_nearest=accept_nearest,
    )
    return compact_tool_result(result)


def step_frames(frames: int = 10) -> dict[str, Any]:
    """Advance the emulator by a small number of frames."""

    _ensure_game_started()
    result = _mcp_server().step_frames(frames=frames, render=True)
    return compact_tool_result(result)


def set_realtime_ticks(enabled: bool = True, fps: float = 60.0) -> dict[str, Any]:
    """Enable or disable realtime ticking while using ADK Web."""

    _ensure_game_started()
    return _mcp_server().set_realtime_ticks(enabled=enabled, fps=fps)


def realtime_tick_status() -> dict[str, Any]:
    """Return realtime tick status."""

    _ensure_game_started()
    return _mcp_server().realtime_tick_status()


def run_rule_based_step() -> dict[str, Any]:
    """Run one safe non-LLM Pokemon control step and return the chosen action."""

    _ensure_game_started()
    client = InProcessPokemonMcpClient()
    result = PokemonAdkLoop(client).run(max_steps=1, checkpoint_every=0)
    return {
        "done": result.get("done"),
        "step_count": result.get("step_count"),
        "mode": result.get("mode"),
        "planned_action": result.get("planned_action"),
        "plan_error": result.get("plan_error"),
        "action_result": compact_tool_result(result.get("action_result", {})),
        "observation": compact_observation(result.get("observation", {})),
    }


def recent_game_commands(limit: int = 50) -> dict[str, Any]:
    """Return the recent MCP/game command log."""

    return _mcp_server().recent_mcp_commands(limit=limit)


def compact_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    for key in ("before_observation", "after_observation"):
        observation = compact.get(key)
        if isinstance(observation, dict):
            compact[key] = compact_observation(observation)
    return compact


def compact_observation(
    observation: dict[str, Any],
    *,
    include_screenshot_base64: bool = False,
) -> dict[str, Any]:
    screenshot = observation.get("screenshot", {})
    screenshot_summary: dict[str, Any] = {
        "format": screenshot.get("format"),
        "width": screenshot.get("width"),
        "height": screenshot.get("height"),
        "base64_length": len(str(screenshot.get("base64", ""))),
    }
    if include_screenshot_base64:
        screenshot_summary["base64"] = screenshot.get("base64")

    return {
        "tool_step_index": observation.get("tool_step_index"),
        "frame_index": observation.get("frame_index"),
        "state": _compact_state(observation.get("state", {})),
        "player_screen_tile": observation.get("player_screen_tile"),
        "player_walk_cell": observation.get("player_walk_cell"),
        "game_area": observation.get("game_area"),
        "game_area_collision": observation.get("game_area_collision"),
        "world_map": observation.get("world_map"),
        "screenshot": screenshot_summary,
        "control_ui": observation.get("control_ui"),
    }


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "map_id": state.get("map_id"),
        "map_name": state.get("map_name"),
        "position": state.get("position"),
        "facing": state.get("facing"),
        "mode": state.get("mode"),
        "in_battle": state.get("in_battle"),
        "dialog_open": state.get("dialog_open"),
        "summary": state.get("summary"),
        "party": state.get("party"),
        "items": state.get("items"),
        "nearby_npcs": state.get("nearby_npcs"),
        "nearby_exits": state.get("nearby_exits"),
    }


def _ensure_game_started() -> None:
    server = _mcp_server()
    if not server.get_session().started:
        server.start_session(window="null", load_fixed=True, control_ui=True)
        server.set_realtime_ticks(enabled=True, fps=60.0)


def _resolve_capture_path(filename: str) -> Path:
    server = _mcp_server()
    now = datetime.now()
    capture_dir = server.get_session().paths.project_root / "captures" / now.strftime("%Y%m%d")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename.strip()).strip("._")
    if not safe_name:
        safe_name = f"adk_web_{now.strftime('%H%M%S_%f')[:-3]}.png"
    if not safe_name.lower().endswith(".png"):
        safe_name += ".png"
    return capture_dir / safe_name


def _mcp_server() -> Any:
    from pokemon_agent import mcp_server

    return mcp_server

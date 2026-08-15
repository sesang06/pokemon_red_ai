from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pokemon_agent.adk_agent.agents.memory_tools import (
    save_memory_entries,
    search_memory_entries,
)
from pokemon_agent.adk_agent.agents.planner.schema import DEFAULT_MAX_STEPS, DEFAULT_OBJECTIVE
from pokemon_agent.adk_agent.runtime.session import PROJECT_ROOT
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState
from pokemon_agent.memory.file_memory import FileLongTermMemory


class _AgentRunnerProcess:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.metadata: dict[str, Any] = {}


_AGENT_RUNNER = _AgentRunnerProcess()


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
    realtime_result = server.set_realtime_ticks(enabled=realtime_ticks, fps=realtime_fps)

    return {
        "session": start_result,
        "realtime": realtime_result,
        "next": "Call observe_game, buttons, or move.",
    }


def stop_game(save_final: bool = False) -> dict[str, Any]:
    """Stop the active Pokemon Red PyBoy session."""

    return _mcp_server().stop_session(save_final=save_final)


def observe_game(include_screenshot_base64: bool = False) -> dict[str, Any]:
    """Observe the current game state, visible world cells, and screenshot metadata."""

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


def buttons(buttons: list[str]) -> dict[str, Any]:
    """Execute a {"type":"buttons","buttons":[...]} action through the realtime ticker."""

    _ensure_game_started()
    return compact_tool_result(_mcp_server().press_buttons(buttons=buttons))


def move(target: list[int]) -> dict[str, Any]:
    """Move toward a current-map world coordinate, re-observing and replanning across screens."""

    _ensure_game_started()
    if not isinstance(target, list) or len(target) != 2:
        raise ValueError("move target must be [x, y]")
    result = _mcp_server().move_to_world_cell(
        target_x=int(target[0]),
        target_y=int(target[1]),
    )
    return compact_tool_result(result)


def wait() -> dict[str, Any]:
    """Wait 300 milliseconds while the realtime ticker advances the game."""

    _ensure_game_started()
    result = _mcp_server().wait()
    return compact_tool_result(result)


def set_realtime_ticks(enabled: bool = True, fps: float = 60.0) -> dict[str, Any]:
    """Enable or disable realtime ticking while using ADK Web."""

    _ensure_game_started()
    return _mcp_server().set_realtime_ticks(enabled=enabled, fps=fps)


def realtime_tick_status() -> dict[str, Any]:
    """Return realtime tick status."""

    _ensure_game_started()
    return _mcp_server().realtime_tick_status()


def recent_game_commands(limit: int = 50) -> dict[str, Any]:
    """Return the recent MCP/game command log."""

    return _mcp_server().recent_mcp_commands(limit=limit)


def agent_runtime_status() -> dict[str, Any]:
    """Return the latest CLI auto-play phase and summary shared with ADK Dev UI."""

    state = _runtime_store().read()
    return {
        key: state.get(key)
        for key in (
            "updated_at",
            "phase",
            "metadata",
            "objective",
            "current_goal",
            "active_action_plan",
            "action_outcome",
            "state_diff",
            "planner_call_count",
            "llm_planner_call_count",
            "interpreter_call_count",
            "done",
            "step_count",
            "mode",
            "stuck_score",
            "history_summary",
            "plan_decision",
            "execution_report",
            "interpretation",
            "plan_error",
            "interpret_error",
        )
    }


def recent_agent_actions(limit: int = 20) -> dict[str, Any]:
    """Return recent structured CLI action_history entries."""

    state = _runtime_store().read()
    bounded_limit = max(1, min(int(limit), 20))
    history = list(state.get("action_history", []))[-bounded_limit:]
    return {
        "updated_at": state.get("updated_at"),
        "phase": state.get("phase"),
        "count": len(history),
        "actions": history,
    }


def start_agent_runner(steps: int = 100, objective: str = DEFAULT_OBJECTIVE) -> dict[str, Any]:
    """Start the real pokemon-adk CLI runner with its normal UI, vision, thinking, and dashboard defaults."""

    bounded_steps = max(1, min(int(steps), DEFAULT_MAX_STEPS))
    normalized_objective = str(objective).strip() or DEFAULT_OBJECTIVE
    with _AGENT_RUNNER.lock:
        existing = _runner_status_locked(log_lines=0)
        if existing.get("running"):
            return {
                **existing,
                "started": False,
                "already_running": True,
                "message": "The pokemon-adk runner is already active. Use agent_runner_status to monitor it.",
            }

        started_at = datetime.now()
        log_path = _runner_log_path(started_at)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "pokemon_agent.adk_agent.runner",
            "--steps",
            str(bounded_steps),
            "--objective",
            normalized_objective,
        ]
        popen_kwargs: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        with log_path.open("a", encoding="utf-8") as output:
            process = subprocess.Popen(command, stdout=output, **popen_kwargs)

        _AGENT_RUNNER.process = process
        _AGENT_RUNNER.metadata = {
            "pid": process.pid,
            "steps": bounded_steps,
            "objective": normalized_objective,
            "started_at": started_at.isoformat(timespec="seconds"),
            "log_path": str(log_path.resolve()),
            "command": command,
        }
        return {
            "started": True,
            "already_running": False,
            "running": True,
            **_public_runner_metadata(_AGENT_RUNNER.metadata),
            "dashboard_url": "http://127.0.0.1:8765/",
            "dev_ui_sessions": ["pokemon-red-planner", "pokemon-red-result-interpreter"],
            "defaults": {
                "window": "SDL2",
                "control_ui": True,
                "realtime_ticks": True,
                "vision": True,
                "thinking_budget": -1,
                "checkpoint_every": 10,
            },
        }


def agent_runner_status(log_lines: int = 20) -> dict[str, Any]:
    """Return progress and recent log output for the pokemon-adk runner started from Dev UI."""

    with _AGENT_RUNNER.lock:
        return _runner_status_locked(log_lines=max(0, min(int(log_lines), 100)))


def search_memory(queries: list[dict[str, str]]) -> dict[str, Any]:
    """Load multiple file-backed map, NPC, Pokemon, or event memories in one call."""

    store = _memory_store()
    return {
        "agent": "pokemon_red_planning_agent",
        "path": str(store.path),
        **search_memory_entries(store, queries),
    }


def save_memory(entries: list[dict[str, str]]) -> dict[str, Any]:
    """Atomically save multiple consolidated map, NPC, Pokemon, or event memories."""

    store = _memory_store()
    return {
        "agent": "pokemon_red_result_interpreter_agent",
        "path": str(store.path),
        **save_memory_entries(store, entries, source="adk_web"),
    }


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
    return {
        "tool_step_index": observation.get("tool_step_index"),
        "frame_index": observation.get("frame_index"),
        "state": _compact_state(observation.get("state", {})),
        "player_screen_tile": observation.get("player_screen_tile"),
        "visible_world_cells": observation.get("visible_world_cells"),
        "safe_neighbor_world_cells": observation.get("safe_neighbor_world_cells"),
        "world_map": observation.get("world_map"),
        "state_events": observation.get("state_events"),
        "screenshot": _compact_image_payload(
            observation.get("screenshot", {}),
            include_base64=include_screenshot_base64,
        ),
        "screenshot_overlay": _compact_image_payload(
            observation.get("screenshot_overlay", {}),
            include_base64=include_screenshot_base64,
        ),
        "control_ui": observation.get("control_ui"),
    }


def _compact_image_payload(image: dict[str, Any], *, include_base64: bool = False) -> dict[str, Any]:
    base64_value = str(image.get("base64", ""))
    summary: dict[str, Any] = {
        "format": image.get("format"),
        "width": image.get("width"),
        "height": image.get("height"),
        "base64_length": len(base64_value),
    }
    for key in (
        "scale",
        "source_width",
        "source_height",
        "tile_columns",
        "tile_rows",
        "walk_cell_columns",
        "walk_cell_rows",
        "walk_cell_size",
        "collision_truthy",
        "coordinate_formula",
        "player_map_position",
        "overlays",
        "legend",
    ):
        if key in image:
            summary[key] = image.get(key)
    if include_base64:
        summary["base64"] = image.get("base64")
    return summary


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "map_id": state.get("map_id"),
        "map_name": state.get("map_name"),
        "position": state.get("position"),
        "facing": state.get("facing"),
        "mode": state.get("mode"),
        "in_battle": state.get("in_battle"),
        "dialog_open": state.get("dialog_open"),
        "block_position": state.get("block_position"),
        "position_detail": state.get("position_detail"),
        "player_name": state.get("player_name"),
        "rival_name": state.get("rival_name"),
        "money": state.get("money"),
        "coins": state.get("coins"),
        "game_time": state.get("game_time"),
        "tileset": state.get("tileset"),
        "pokedex_caught": state.get("pokedex_caught"),
        "badges": state.get("badges"),
        "warps": state.get("warps"),
        "dialog_text": state.get("dialog_text"),
        "dialog": state.get("dialog"),
        "menu": state.get("menu"),
        "map": state.get("map"),
        "counts": state.get("counts"),
        "events": state.get("events"),
        "summary": state.get("summary"),
        "party": state.get("party"),
        "items": state.get("items"),
        "nearby_npcs": state.get("nearby_npcs"),
        "nearby_exits": state.get("nearby_exits"),
    }
    if bool(state.get("in_battle")) and isinstance(state.get("battle"), dict):
        battle = state["battle"]
        opponent = battle.get("opponent") if isinstance(battle.get("opponent"), dict) else None
        compact["battle"] = {
            "active": True,
            "type": battle.get("type"),
            "kind": battle.get("kind"),
            "turns": battle.get("turns"),
            **({"opponent": _compact_battle_opponent(opponent)} if opponent is not None else {}),
        }
    return compact


def _compact_battle_opponent(opponent: dict[str, Any]) -> dict[str, Any]:
    return {
        key: opponent.get(key)
        for key in ("species", "level", "hp", "max_hp", "status", "types")
        if opponent.get(key) is not None
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


def _memory_store() -> FileLongTermMemory:
    return FileLongTermMemory()


def _runtime_store() -> FileAgentRuntimeState:
    return FileAgentRuntimeState()


def _runner_status_locked(*, log_lines: int) -> dict[str, Any]:
    process = _AGENT_RUNNER.process
    if process is None:
        return {"status": "not_started", "running": False}

    return_code = process.poll()
    metadata = _public_runner_metadata(_AGENT_RUNNER.metadata)
    runtime = _runtime_store().read()
    result: dict[str, Any] = {
        "status": "running" if return_code is None else "completed" if return_code == 0 else "failed",
        "running": return_code is None,
        "return_code": return_code,
        **metadata,
        "progress": {
            "step_count": runtime.get("step_count"),
            "max_steps": metadata.get("steps"),
            "phase": runtime.get("phase"),
            "mode": runtime.get("mode"),
            "done": runtime.get("done"),
            "termination_reason": runtime.get("termination_reason"),
            "updated_at": runtime.get("updated_at"),
        },
        "dashboard_url": "http://127.0.0.1:8765/",
        "dev_ui_sessions": ["pokemon-red-planner", "pokemon-red-result-interpreter"],
    }
    if log_lines:
        result["log_tail"] = _runner_log_tail(metadata.get("log_path"), limit=log_lines)
    return result


def _public_runner_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in ("pid", "steps", "objective", "started_at", "log_path")
        if metadata.get(key) is not None
    }


def _runner_log_path(started_at: datetime) -> Path:
    return (
        PROJECT_ROOT
        / "logs"
        / "adk_web"
        / started_at.strftime("%Y%m%d")
        / f"runner_{started_at.strftime('%H%M%S_%f')[:-3]}.log"
    )


def _runner_log_tail(path: Any, *, limit: int) -> list[str]:
    if not path:
        return []
    try:
        return Path(str(path)).read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []

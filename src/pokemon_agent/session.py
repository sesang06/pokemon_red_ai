from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, cast

from pokemon_agent.agent.actions import Button, ButtonAction
from pokemon_agent.emulator.pyboy_env import PyBoyEnvironment
from pokemon_agent.memory.memory_reader import PokemonRedMemoryReader
from pokemon_agent.memory.ram_map import format_ram_watch
from pokemon_agent.memory.world_map import WorldMapTracker
from pokemon_agent.memory.world_state import GameMode, GameState, Position
from pokemon_agent.tools.pathfinding import directions_from_path
from pokemon_agent.tools.screen_navigation import (
    PLAYER_SCREEN_TILE,
    PLAYER_WALK_CELL,
    grid_point_dict,
    matrix_to_rows,
    plan_screen_path,
)
from pokemon_agent.vision.game_area import format_game_area_collision_watch, format_game_area_watch

ButtonName = Literal["a", "b", "start", "select", "left", "right", "up", "down"]
StateKind = Literal["fixed", "snapshot", "last"]

VALID_BUTTONS: set[str] = {"a", "b", "start", "select", "left", "right", "up", "down"}


class PokemonEnvironment(Protocol):
    memory: Any

    def button(self, button: str, frames: int = 1) -> None:
        """Press a Game Boy button."""

    def tick(self, frames: int = 1, render: bool = False) -> bool:
        """Advance the emulator."""

    def screen_image(self) -> Any:
        """Return the current screen as a Pillow image."""

    def game_area(self) -> Any:
        """Return PyBoy.game_area()."""

    def game_area_collision(self) -> Any:
        """Return PyBoy.game_area_collision()."""

    def save_state(self, path: Path) -> None:
        """Save a PyBoy state."""

    def load_state(self, path: Path) -> None:
        """Load a PyBoy state."""

    def stop(self, save: bool = False) -> None:
        """Stop the emulator."""


@dataclass(frozen=True)
class PokemonSessionPaths:
    project_root: Path
    rom: Path
    state_dir: Path
    fixed_state: Path
    last_state: Path


def default_session_paths() -> PokemonSessionPaths:
    source_root = Path(__file__).resolve().parents[2]
    project_root = Path.cwd() if (Path.cwd() / "src" / "pokered.gb").exists() else source_root
    state_dir = project_root / "states"
    return PokemonSessionPaths(
        project_root=project_root,
        rom=project_root / "src" / "pokered.gb",
        state_dir=state_dir,
        fixed_state=state_dir / "fixed_start.state",
        last_state=state_dir / "last.state",
    )


class PokemonSession:
    def __init__(
        self,
        paths: PokemonSessionPaths | None = None,
        env_factory: Callable[[Path, str], PokemonEnvironment] | None = None,
        reader: PokemonRedMemoryReader | None = None,
    ):
        self.paths = paths or default_session_paths()
        self._env_factory = env_factory or (lambda rom_path, window: PyBoyEnvironment(rom_path, window=window))
        self.reader = reader or PokemonRedMemoryReader()
        self.world_map_tracker = WorldMapTracker()
        self.env: PokemonEnvironment | None = None
        self.control_panel: Any | None = None
        self.mcp_log_provider: Callable[[], str] | None = None
        self._last_control_panel_update = 0.0
        self.realtime_ticks_enabled = False
        self.realtime_tick_fps = 60.0
        self.realtime_max_frames_per_pump = 12
        self._last_realtime_tick_at = 0.0
        self.window = "null"
        self.tool_step_index = 0
        self.frame_index = 0

    @property
    def started(self) -> bool:
        return self.env is not None

    def start(self, window: str = "null", load_fixed: bool = True, control_ui: bool = False) -> dict[str, Any]:
        if self.env is not None:
            if control_ui:
                self._ensure_control_panel()
                self._refresh_control_panel(force=True)
            return {
                "started": True,
                "already_started": True,
                "rom": str(self.paths.rom),
                "fixed_state": str(self.paths.fixed_state),
                "window": self.window,
                "control_ui": self.control_panel is not None,
            }

        if not self.paths.rom.exists():
            raise FileNotFoundError(f"ROM not found: {self.paths.rom}")

        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        self.window = window
        self.env = self._env_factory(self.paths.rom, window)

        if load_fixed:
            if not self.paths.fixed_state.exists():
                self._tick(1, render=True)
                self.env.save_state(self.paths.fixed_state)
            self.env.load_state(self.paths.fixed_state)
            self._tick(1, render=True)

        if control_ui:
            self._ensure_control_panel()
            self._refresh_control_panel(force=True)

        return {
            "started": True,
            "already_started": False,
            "rom": str(self.paths.rom),
            "fixed_state": str(self.paths.fixed_state),
            "window": self.window,
            "loaded_fixed": load_fixed,
            "control_ui": self.control_panel is not None,
        }

    def set_mcp_log_provider(self, provider: Callable[[], str] | None) -> None:
        self.mcp_log_provider = provider

    def set_realtime_ticking(
        self,
        enabled: bool,
        *,
        fps: float = 60.0,
        max_frames_per_pump: int = 12,
    ) -> dict[str, Any]:
        fps = _bounded_float(fps, minimum=1.0, maximum=240.0, name="fps")
        max_frames_per_pump = _bounded_int(max_frames_per_pump, minimum=1, maximum=120, name="max_frames_per_pump")
        self.realtime_ticks_enabled = bool(enabled)
        self.realtime_tick_fps = fps
        self.realtime_max_frames_per_pump = max_frames_per_pump
        self._last_realtime_tick_at = time.monotonic()
        self._refresh_control_panel(force=True)
        return self.realtime_tick_status()

    def realtime_tick_status(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "enabled": self.realtime_ticks_enabled,
            "fps": self.realtime_tick_fps,
            "max_frames_per_pump": self.realtime_max_frames_per_pump,
            "frame_index": self.frame_index,
        }

    def pump_realtime(self, *, now: float | None = None) -> dict[str, Any]:
        if self.env is None:
            return {**self.realtime_tick_status(), "frames_ticked": 0, "running": False}

        current = time.monotonic() if now is None else now
        frames = 0
        running = True
        if self.realtime_ticks_enabled:
            if self._last_realtime_tick_at <= 0:
                self._last_realtime_tick_at = current
            else:
                elapsed = max(0.0, current - self._last_realtime_tick_at)
                frames = min(int(elapsed * self.realtime_tick_fps), self.realtime_max_frames_per_pump)
                if frames > 0:
                    running = self._tick(frames, render=True)
                    self._last_realtime_tick_at = current

        self._refresh_control_panel()
        return {
            **self.realtime_tick_status(),
            "frames_ticked": frames,
            "running": running,
        }

    def stop(self, save_final: bool = False) -> dict[str, Any]:
        env = self.env
        if env is None:
            return {"stopped": True, "already_stopped": True}

        saved_path = None
        if save_final:
            env.save_state(self.paths.last_state)
            saved_path = self.paths.last_state
            self._notify_control_panel_saved(self.paths.last_state)

        env.stop(save=False)
        self.env = None
        self._close_control_panel()
        return {
            "stopped": True,
            "already_stopped": False,
            "saved_path": None if saved_path is None else str(saved_path),
        }

    def observe(self) -> dict[str, Any]:
        env = self._require_env()
        state = self.reader.read(env.memory)
        game_area = matrix_to_rows(env.game_area())
        collision = matrix_to_rows(env.game_area_collision())
        screenshot = self._screenshot()
        observation = {
            "tool_step_index": self.tool_step_index,
            "frame_index": self.frame_index,
            "state": _game_state_dict(state),
            "ram": state.raw,
            "ram_watch": format_ram_watch(env.memory),
            "game_area": game_area,
            "game_area_collision": collision,
            "player_screen_tile": grid_point_dict(PLAYER_SCREEN_TILE),
            "player_walk_cell": grid_point_dict(PLAYER_WALK_CELL),
            "screenshot": screenshot,
            "control_ui": self.control_panel is not None,
        }
        observation["world_map"] = self.world_map_tracker.update_from_observation(observation)
        self._refresh_control_panel(state)

        return observation

    def press_button(
        self,
        button: str,
        frames: int = 4,
        after_frames: int = 8,
    ) -> dict[str, Any]:
        action = self._coerce_action({"button": button, "frames": frames, "after_frames": after_frames})
        self._execute_action(action)
        self.tool_step_index += 1
        return {"executed_actions": [_action_dict(action)], "after_observation": self.observe()}

    def execute_actions(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        coerced = [self._coerce_action(action) for action in actions]
        if len(coerced) > 16:
            raise ValueError("execute_actions accepts at most 16 actions")

        for action in coerced:
            self._execute_action(action)
        self.tool_step_index += 1
        return {"executed_actions": [_action_dict(action) for action in coerced], "after_observation": self.observe()}

    def step_frames(self, frames: int = 1, render: bool = False) -> dict[str, Any]:
        frames = _bounded_int(frames, minimum=1, maximum=600, name="frames")
        running = self._tick(frames, render=render)
        self.tool_step_index += 1
        return {"running": running, "frames": frames, "after_observation": self.observe()}

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        env = self._require_env()
        state_path = self._resolve_state_path(kind, path, for_save=True)
        env.save_state(state_path)
        self._notify_control_panel_saved(state_path)
        return {"saved": True, "kind": kind, "path": str(state_path)}

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        env = self._require_env()
        state_path = self._resolve_state_path(kind, path, for_save=False)
        if not state_path.exists():
            raise FileNotFoundError(f"state not found: {state_path}")
        env.load_state(state_path)
        self._notify_control_panel_loaded(state_path)
        self.tool_step_index += 1
        return {"loaded": True, "kind": kind, "path": str(state_path), "after_observation": self.observe()}

    def reset_to_fixed(self) -> dict[str, Any]:
        return self.load_state(kind="fixed")

    def move_to_screen_tile(
        self,
        target_x: int,
        target_y: int,
        max_steps: int = 8,
        accept_nearest: bool = True,
    ) -> dict[str, Any]:
        max_steps = _bounded_int(max_steps, minimum=0, maximum=64, name="max_steps")
        before = self.observe()
        plan = plan_screen_path(
            target_x,
            target_y,
            before["game_area_collision"],
            start=PLAYER_WALK_CELL,
            accept_nearest=accept_nearest,
        )

        requested_target = {
            "screen_tile": grid_point_dict(plan.requested_screen_tile),
            "walk_cell": grid_point_dict(plan.requested_walk_cell),
        }
        resolved_target = {
            "screen_tile": grid_point_dict(plan.resolved_screen_tile),
            "walk_cell": grid_point_dict(plan.resolved_walk_cell),
        }
        planned_path = [grid_point_dict(point) for point in plan.path]
        planned_screen_path = [grid_point_dict(point) for point in map(_walk_cell_to_screen_dict_point, plan.path)]
        executed_actions: list[dict[str, Any]] = []
        stop_reason = plan.stop_reason

        world_goal = _world_goal_from_observation(before, plan.resolved_walk_cell)
        if stop_reason == "path_found":
            directions = directions_from_path(plan.path)
            stop_reason = "target_reached" if not directions else "planned_path_exhausted"

            for direction in directions[:max_steps]:
                current = self.observe()
                interruption = _interruption_reason(current)
                if interruption is not None:
                    stop_reason = interruption
                    break

                if world_goal is not None and _position_from_observation(current) == world_goal:
                    stop_reason = "target_reached"
                    break

                action = ButtonAction(cast(Button, direction), frames=4, after_frames=12)
                self._execute_action(action)
                executed_actions.append(_action_dict(action))
                self.tool_step_index += 1

                after_step = self.observe()
                interruption = _interruption_reason(after_step)
                if interruption is not None:
                    stop_reason = interruption
                    break
                if world_goal is not None and _position_from_observation(after_step) == world_goal:
                    stop_reason = "target_reached"
                    break
            else:
                if len(executed_actions) < len(directions):
                    stop_reason = "max_steps_reached"
                elif world_goal is None:
                    stop_reason = "planned_path_exhausted"

        after = self.observe()
        return {
            "requested_target": requested_target,
            "resolved_target": resolved_target,
            "planned_path": planned_path,
            "planned_screen_path": planned_screen_path,
            "executed_actions": executed_actions,
            "steps_taken": len(executed_actions),
            "stop_reason": stop_reason,
            "before_observation": before,
            "after_observation": after,
        }

    def _execute_action(self, action: ButtonAction) -> None:
        env = self._require_env()
        env.button(action.button, frames=action.frames)
        self._tick(max(action.frames, 1), render=True)
        if action.after_frames > 0:
            self._tick(action.after_frames, render=True)

    def _tick(self, frames: int, render: bool = False) -> bool:
        env = self._require_env()
        running = env.tick(frames, render=render or self._should_render_ticks())
        self.frame_index += frames
        return running

    def _should_render_ticks(self) -> bool:
        return True

    def _screenshot(self) -> dict[str, Any]:
        image = self._require_env().screen_image()
        width, height = image.size
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return {
            "format": "png",
            "width": int(width),
            "height": int(height),
            "base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        }

    def _coerce_action(self, action: dict[str, Any]) -> ButtonAction:
        button = str(action.get("button", ""))
        if button not in VALID_BUTTONS:
            raise ValueError(f"invalid button: {button}")
        frames = _bounded_int(action.get("frames", 1), minimum=1, maximum=60, name="frames")
        after_frames = _bounded_int(action.get("after_frames", 8), minimum=0, maximum=180, name="after_frames")
        return ButtonAction(cast(Button, button), frames=frames, after_frames=after_frames)

    def _resolve_state_path(self, kind: str, path: str | None, *, for_save: bool) -> Path:
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        if path is not None:
            return self._safe_state_path(path)
        if kind == "fixed":
            return self.paths.fixed_state
        if kind == "last":
            return self.paths.last_state
        if kind == "snapshot":
            if not for_save:
                return self.paths.last_state
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            state_name = _safe_name(self.observe()["state"]["map_name"])
            return self.paths.state_dir / f"snapshot_{timestamp}_{state_name}.state"
        raise ValueError(f"unknown state kind: {kind}")

    def _safe_state_path(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.paths.state_dir / candidate
        resolved = candidate.resolve()
        state_dir = self.paths.state_dir.resolve()
        if not resolved.is_relative_to(state_dir):
            raise ValueError(f"state path must stay inside {state_dir}")
        return resolved

    def _ensure_control_panel(self) -> None:
        if self.control_panel is not None:
            return
        from pokemon_agent.ui.control_panel import QtStateControlPanel

        self.control_panel = QtStateControlPanel(self.paths.state_dir, self.paths.fixed_state)
        self._last_control_panel_update = 0.0

    def _refresh_control_panel(self, state: GameState | None = None, *, force: bool = False) -> None:
        if self.control_panel is None or self.env is None:
            return

        try:
            if state is None:
                state = self.reader.read(self.env.memory)

            self.control_panel.update_screen_image(self.env.screen_image())

            now = time.monotonic()
            if force or now - self._last_control_panel_update >= 1.0:
                self.control_panel.update_ram_text(format_ram_watch(self.env.memory))
                self.control_panel.update_game_area_text(format_game_area_watch(self.env))
                self.control_panel.update_collision_text(format_game_area_collision_watch(self.env))
                self.control_panel.update_world_map_text(self.world_map_tracker.current_ascii())
                if self.mcp_log_provider is not None:
                    self.control_panel.update_mcp_log_text(self.mcp_log_provider())
                self._last_control_panel_update = now

            commands = self.control_panel.poll(state)
            self._handle_control_panel_commands(commands)
        except Exception as exc:
            if self.control_panel is not None:
                self.control_panel.notify_error(f"{type(exc).__name__}: {exc}")

    def pump_control_panel(self) -> None:
        self._refresh_control_panel()

    def _handle_control_panel_commands(self, commands: list[Any]) -> None:
        for command in commands:
            if command.action == "save_state":
                if command.path is None:
                    continue
                self._require_env().save_state(command.path)
                self._notify_control_panel_saved(command.path)
            elif command.action == "load_state":
                if command.path is None:
                    continue
                if not command.path.exists():
                    self._notify_control_panel_error(f"not found: {command.path}")
                    continue
                self._require_env().load_state(command.path)
                self._notify_control_panel_loaded(command.path)
            elif command.action == "stop":
                self.stop(save_final=False)
                return

    def _notify_control_panel_saved(self, path: Path) -> None:
        if self.control_panel is not None:
            self.control_panel.notify_saved(path)

    def _notify_control_panel_loaded(self, path: Path) -> None:
        if self.control_panel is not None:
            self.control_panel.notify_loaded(path)

    def _notify_control_panel_error(self, message: str) -> None:
        if self.control_panel is not None:
            self.control_panel.notify_error(message)

    def _close_control_panel(self) -> None:
        if self.control_panel is None:
            return
        self.control_panel.close()
        self.control_panel = None

    def _require_env(self) -> PokemonEnvironment:
        if self.env is None:
            raise RuntimeError("Pokemon session is not started. Call start_session first.")
        return self.env


def _game_state_dict(state: GameState) -> dict[str, Any]:
    return {
        "map_id": state.map_id,
        "map_name": state.map_name,
        "position": None if state.position is None else {"x": state.position.x, "y": state.position.y},
        "facing": state.facing,
        "mode": state.mode.value if isinstance(state.mode, GameMode) else str(state.mode),
        "in_battle": state.in_battle,
        "dialog_open": state.dialog_open,
        "money": state.money,
        "party": [member.__dict__ for member in state.party],
        "items": [item.__dict__ for item in state.items],
        "nearby_npcs": [npc.__dict__ for npc in state.nearby_npcs],
        "nearby_exits": [exit_observation.__dict__ for exit_observation in state.nearby_exits],
        "flags": dict(state.flags),
        "raw": dict(state.raw),
        "summary": state.summary(),
    }


def _action_dict(action: ButtonAction) -> dict[str, Any]:
    return {
        "button": action.button,
        "frames": action.frames,
        "after_frames": action.after_frames,
    }


def _bounded_int(value: Any, *, minimum: int, maximum: int, name: str) -> int:
    converted = int(value)
    if converted < minimum or converted > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return converted


def _bounded_float(value: Any, *, minimum: float, maximum: float, name: str) -> float:
    converted = float(value)
    if converted < minimum or converted > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return converted


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return normalized.strip("_") or "unknown"


def _walk_cell_to_screen_dict_point(point: Any) -> Any:
    from pokemon_agent.tools.screen_navigation import walk_cell_to_screen_tile

    return walk_cell_to_screen_tile(point)


def _position_from_observation(observation: dict[str, Any]) -> Position | None:
    position = observation.get("state", {}).get("position")
    if position is None:
        return None
    return Position(x=int(position["x"]), y=int(position["y"]))


def _world_goal_from_observation(observation: dict[str, Any], target_walk_cell: Any) -> Position | None:
    position = _position_from_observation(observation)
    if position is None:
        return None
    return Position(
        x=position.x + target_walk_cell.x - PLAYER_WALK_CELL.x,
        y=position.y + target_walk_cell.y - PLAYER_WALK_CELL.y,
    )


def _interruption_reason(observation: dict[str, Any]) -> str | None:
    state = observation.get("state", {})
    mode = state.get("mode")
    if state.get("in_battle") or mode == "battle":
        return "interrupted_battle"
    if state.get("dialog_open") or mode == "talk":
        return "interrupted_dialog"
    if mode == "inventory":
        return "interrupted_menu"
    return None

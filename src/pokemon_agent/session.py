from __future__ import annotations

import base64
import copy
import re
import threading
import time
import weakref
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Protocol

from pokemon_agent.emulator.pyboy_env import PyBoyEnvironment
from pokemon_agent.input_contract import (
    BUTTON_TOKENS,
    MAX_BUTTONS_PER_ACTION,
    MAX_MOVE_PATH_STEPS,
    MAX_WORLD_NAVIGATION_SEGMENTS,
)
from pokemon_agent.memory.memory_reader import PokemonRedMemoryReader
from pokemon_agent.memory.ram_map import format_ram_watch
from pokemon_agent.memory.world_map import WorldMapTracker
from pokemon_agent.memory.world_state import GameMode, GameState, Position
from pokemon_agent.tools.pathfinding import GridPoint, directions_from_path
from pokemon_agent.tools.screen_navigation import (
    PLAYER_SCREEN_TILE,
    PLAYER_WALK_CELL,
    compress_collision_to_walk_grid,
    grid_point_dict,
    matrix_to_rows,
    plan_screen_path,
    map_position_to_walk_cell,
    walk_cell_to_map_position,
    walk_cell_to_screen_tile,
)
from pokemon_agent.vision.game_area import format_game_area_collision_watch, format_game_area_watch
from pokemon_agent.vision.overlay import overlay_metadata, render_collision_overlay

ButtonName = Literal["a", "b", "start", "select", "left", "right", "up", "down"]
StateKind = Literal["fixed", "snapshot", "last"]

VALID_BUTTON_TOKENS = frozenset(BUTTON_TOKENS)
DEFAULT_REALTIME_FPS = 60.0
DEFAULT_SNAPSHOT_HZ = 30.0
BUTTON_HOLD_FRAMES = 4
INPUT_AFTER_FRAMES = 20
ACTION_WAIT_SECONDS = 0.3
MOVE_STEP_TIMEOUT_SECONDS = 1.0


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


@dataclass
class _EmulatorCommand:
    kind: str
    args: tuple[Any, ...] = ()
    completed: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class _FifoActionGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._next_ticket = 0
        self._serving = 0

    @contextmanager
    def turn(self) -> Iterator[None]:
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            while ticket != self._serving:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                self._serving += 1
                self._condition.notify_all()


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
        self.realtime_ticks_enabled = True
        self.realtime_tick_fps = DEFAULT_REALTIME_FPS
        self.snapshot_refresh_hz = DEFAULT_SNAPSHOT_HZ
        self._next_realtime_tick_at = 0.0
        self._next_visual_snapshot_at = 0.0
        self._last_reported_frame_index = 0
        self._env_lock = threading.RLock()
        self._frame_condition = threading.Condition(self._env_lock)
        self._snapshot_condition = threading.Condition(threading.RLock())
        self._action_gate = _FifoActionGate()
        self._emulator_commands: deque[_EmulatorCommand] = deque()
        self._realtime_thread: threading.Thread | None = None
        self._realtime_stop_event = threading.Event()
        self._realtime_running = True
        self._realtime_error: str | None = None
        self._closing = False
        self._latest_observation: dict[str, Any] | None = None
        self._latest_ui_payload: dict[str, Any] | None = None
        self._pending_state_events: deque[dict[str, Any]] = deque(maxlen=256)
        self._observation_listeners: list[Callable[[dict[str, Any]], None]] = []
        self.window = "null"
        self.tool_step_index = 0
        self.frame_index = 0
        self._previous_state_snapshot: dict[str, Any] | None = None
        self._blocked_world_edges: dict[int, dict[tuple[tuple[int, int], tuple[int, int]], int]] = {}
        self._navigation_attempt_index = 0

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
        with self._env_lock:
            self.window = "qt" if control_ui and window == "SDL2" else window
            self.env = self._env_factory(self.paths.rom, self.window)
            self._previous_state_snapshot = None
            self._blocked_world_edges.clear()
            self._navigation_attempt_index = 0
            self._realtime_running = True
            self._realtime_error = None
            self._closing = False
            self.realtime_ticks_enabled = True
            self._next_realtime_tick_at = time.monotonic()
            self._next_visual_snapshot_at = 0.0
            self._last_reported_frame_index = self.frame_index
            self._emulator_commands.clear()
            self._latest_observation = None
            self._latest_ui_payload = None
            self._pending_state_events.clear()

            if load_fixed:
                if not self.paths.fixed_state.exists():
                    self._tick(1, render=True)
                    self.env.save_state(self.paths.fixed_state)
                self.env.load_state(self.paths.fixed_state)
                self._tick(1, render=True)

        self._capture_cached_observation(force_visual=True)
        self._start_realtime_worker()

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

    def add_observation_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._snapshot_condition:
            if listener not in self._observation_listeners:
                self._observation_listeners.append(listener)

    def remove_observation_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._snapshot_condition:
            if listener in self._observation_listeners:
                self._observation_listeners.remove(listener)

    def peek_observation(self) -> dict[str, Any]:
        with self._snapshot_condition:
            if self._latest_observation is None:
                raise RuntimeError("The realtime ticker has not produced an observation yet.")
            return copy.deepcopy(self._latest_observation)

    def set_realtime_ticking(
        self,
        enabled: bool,
        *,
        fps: float = 60.0,
    ) -> dict[str, Any]:
        fps = _bounded_float(fps, minimum=1.0, maximum=240.0, name="fps")
        with self._frame_condition:
            self.realtime_ticks_enabled = bool(enabled)
            self.realtime_tick_fps = fps
            self._next_realtime_tick_at = time.monotonic()
            self._realtime_error = None
            self._realtime_running = self.env is not None
            self._frame_condition.notify_all()
        if enabled:
            self._start_realtime_worker()
        self._refresh_control_panel(force=True)
        return self.realtime_tick_status()

    def realtime_tick_status(self) -> dict[str, Any]:
        with self._env_lock:
            return {
                "started": self.started,
                "enabled": self.realtime_ticks_enabled,
                "fps": self.realtime_tick_fps,
                "snapshot_hz": self.snapshot_refresh_hz,
                "frame_index": self.frame_index,
                "ticker_alive": self._realtime_thread is not None and self._realtime_thread.is_alive(),
                "ticker_error": self._realtime_error,
            }

    def pump_realtime(self) -> dict[str, Any]:
        with self._env_lock:
            frames = max(0, self.frame_index - self._last_reported_frame_index)
            self._last_reported_frame_index = self.frame_index
            running = self.env is not None and self._realtime_running
        self._refresh_control_panel()
        return {
            **self.realtime_tick_status(),
            "frames_ticked": frames,
            "running": running,
        }

    def _wait_for_snapshot_after(self, frame_index: int, timeout: float = 0.25) -> bool:
        deadline = time.monotonic() + timeout
        with self._snapshot_condition:
            while True:
                latest_frame = int((self._latest_observation or {}).get("frame_index", -1))
                if latest_frame > frame_index:
                    return True
                if self._closing or self._realtime_error or not self.realtime_ticks_enabled:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._snapshot_condition.wait(timeout=min(0.05, remaining))

    def _start_realtime_worker(self) -> None:
        with self._frame_condition:
            if self._realtime_thread is not None and self._realtime_thread.is_alive():
                return
            self._realtime_stop_event = threading.Event()
            session_ref = weakref.ref(self)
            thread = threading.Thread(
                target=PokemonSession._realtime_worker_main,
                args=(session_ref, self._realtime_stop_event),
                name="pokemon-realtime-ticker",
                daemon=True,
            )
            self._realtime_thread = thread
            thread.start()

    def _stop_realtime_worker(self) -> None:
        with self._frame_condition:
            thread = self._realtime_thread
            self.realtime_ticks_enabled = False
            self._realtime_stop_event.set()
            self._frame_condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._frame_condition:
            if self._realtime_thread is thread:
                self._realtime_thread = None

    @staticmethod
    def _realtime_worker_main(
        session_ref: "weakref.ReferenceType[PokemonSession]",
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            session = session_ref()
            if session is None:
                return
            try:
                delay = session._advance_realtime_once()
            except Exception as exc:
                with session._frame_condition:
                    session._realtime_error = f"{type(exc).__name__}: {exc}"
                    session._realtime_running = False
                    session.realtime_ticks_enabled = False
                    session._fail_pending_commands(exc)
                    session._frame_condition.notify_all()
                with session._snapshot_condition:
                    session._snapshot_condition.notify_all()
                return
            del session
            stop_event.wait(delay)

    def _advance_realtime_once(self) -> float:
        command = None
        with self._frame_condition:
            if self._emulator_commands:
                command = self._emulator_commands.popleft()
            if command is None and (self.env is None or not self.realtime_ticks_enabled):
                return 0.01

        if command is not None:
            self._execute_emulator_command(command)
            return 0.0

        with self._frame_condition:
            if self.env is None or not self.realtime_ticks_enabled:
                return 0.01
            now = time.monotonic()
            interval = 1.0 / self.realtime_tick_fps
            if self._next_realtime_tick_at <= 0:
                self._next_realtime_tick_at = now
            if now < self._next_realtime_tick_at:
                return min(0.01, max(0.001, self._next_realtime_tick_at - now))

            self._realtime_running = self._tick(1, render=True)
            self._next_realtime_tick_at += interval
            if self._next_realtime_tick_at <= now:
                self._next_realtime_tick_at = now + interval
            if not self._realtime_running:
                self.realtime_ticks_enabled = False

        self._capture_cached_observation(force_visual=now >= self._next_visual_snapshot_at)
        return min(0.01, max(0.001, self._next_realtime_tick_at - time.monotonic()))

    def _submit_emulator_command(self, kind: str, *args: Any) -> Any:
        command = _EmulatorCommand(kind=kind, args=tuple(args))
        with self._frame_condition:
            if self.env is None:
                raise RuntimeError("Pokemon session is not started. Call start_session first.")
            if self._realtime_error:
                raise RuntimeError(f"Realtime ticker failed: {self._realtime_error}")
            self._emulator_commands.append(command)
            self._frame_condition.notify_all()
        self._start_realtime_worker()

        while not command.completed.wait(timeout=0.05):
            with self._frame_condition:
                thread_alive = self._realtime_thread is not None and self._realtime_thread.is_alive()
                if self._realtime_error or not thread_alive:
                    raise RuntimeError(self._realtime_error or "Realtime ticker stopped before processing a command.")
        if command.error is not None:
            raise command.error
        return command.result

    def _execute_emulator_command(self, command: _EmulatorCommand) -> None:
        try:
            with self._env_lock:
                env = self._require_env()
                if command.kind == "button":
                    env.button(str(command.args[0]), frames=int(command.args[1]))
                elif command.kind == "save_state":
                    env.save_state(command.args[0])
                elif command.kind == "load_state":
                    env.load_state(command.args[0])
                    self._previous_state_snapshot = None
                    with self._snapshot_condition:
                        self._pending_state_events.clear()
                    self._next_visual_snapshot_at = 0.0
                elif command.kind == "stop":
                    env.stop(save=False)
                    self.env = None
                    self._realtime_running = False
                    self.realtime_ticks_enabled = False
                else:
                    raise ValueError(f"unknown emulator command: {command.kind}")

            if command.kind == "load_state":
                self._capture_cached_observation(force_visual=True)
            command.result = True
        except BaseException as exc:
            command.error = exc
        finally:
            command.completed.set()

    def _fail_pending_commands(self, exc: BaseException) -> None:
        while self._emulator_commands:
            command = self._emulator_commands.popleft()
            command.error = exc
            command.completed.set()

    def _ensure_realtime_ticking(self) -> None:
        with self._frame_condition:
            if self.env is None:
                raise RuntimeError("Pokemon session is not started. Call start_session first.")
            if self._closing:
                raise RuntimeError("Pokemon session is stopping.")
            if not self.realtime_ticks_enabled:
                self.realtime_ticks_enabled = True
                self._next_realtime_tick_at = time.monotonic()
                self._realtime_running = True
                self._realtime_error = None
                self._frame_condition.notify_all()
        self._start_realtime_worker()

    def _wait_realtime(self, seconds: float, *, start_frame: int | None = None) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        with self._frame_condition:
            while True:
                if self._closing or self.env is None or self._realtime_error or not self.realtime_ticks_enabled:
                    return False
                now = time.monotonic()
                frame_progressed = start_frame is None or self.frame_index > start_frame
                if now >= deadline and frame_progressed:
                    return True
                self._frame_condition.wait(timeout=max(0.001, min(0.05, deadline - now if now < deadline else 0.01)))

    def _wait_realtime_frames(self, frames: int, *, start_frame: int) -> bool:
        target_frame = start_frame + max(0, int(frames))
        with self._frame_condition:
            while True:
                if self._closing or self.env is None or self._realtime_error or not self.realtime_ticks_enabled:
                    return False
                if self.frame_index >= target_frame:
                    return True
                self._frame_condition.wait(timeout=0.05)

    def _realtime_stop_reason(self) -> str:
        return "realtime_ticker_stopped"

    def stop(self, save_final: bool = False) -> dict[str, Any]:
        with self._frame_condition:
            self._closing = True
            self._frame_condition.notify_all()
        with self._action_gate.turn():
            if self.env is None:
                self._stop_realtime_worker()
                return {"stopped": True, "already_stopped": True}

            saved_path = None
            ticker_alive = self._realtime_thread is not None and self._realtime_thread.is_alive()
            if self._realtime_error or not ticker_alive:
                with self._env_lock:
                    env = self._require_env()
                    if save_final:
                        env.save_state(self.paths.last_state)
                        saved_path = self.paths.last_state
                    env.stop(save=False)
                    self.env = None
                    self._realtime_running = False
                    self.realtime_ticks_enabled = False
            else:
                if save_final:
                    self._submit_emulator_command("save_state", self.paths.last_state)
                    saved_path = self.paths.last_state
                self._submit_emulator_command("stop")
            if saved_path is not None:
                self._notify_control_panel_saved(saved_path)
            self._stop_realtime_worker()
            self._previous_state_snapshot = None
            self._close_control_panel()
            return {
                "stopped": True,
                "already_stopped": False,
                "saved_path": None if saved_path is None else str(saved_path),
            }

    def observe(self, *, refresh_control_panel: bool = True) -> dict[str, Any]:
        if self.env is None:
            raise RuntimeError("Pokemon session is not started. Call start_session first.")
        tool_step_index = self.tool_step_index
        with self._snapshot_condition:
            if self._latest_observation is None:
                raise RuntimeError("The realtime ticker has not produced an observation yet.")
            observation = copy.deepcopy(self._latest_observation)
            state_events = [dict(event) for event in self._pending_state_events]
            self._pending_state_events.clear()
        observation["state_events"] = state_events
        observation["state"]["events"] = state_events
        observation["tool_step_index"] = tool_step_index
        if refresh_control_panel:
            self._refresh_control_panel()
        return observation

    def _capture_cached_observation(self, *, force_visual: bool = False) -> None:
        with self._env_lock:
            env = self._require_env()
            state = self.reader.read(env.memory)
            state_dict = _game_state_dict(state)
            state_events = _state_events(self._previous_state_snapshot, state_dict)
            self._previous_state_snapshot = _state_snapshot(state_dict)
            state_dict["events"] = state_events

            with self._snapshot_condition:
                previous = self._latest_observation
                previous_ui = self._latest_ui_payload
            refresh_visual = force_visual or previous is None
            if refresh_visual:
                game_area = matrix_to_rows(env.game_area())
                collision = matrix_to_rows(env.game_area_collision())
                walk_area_collision = compress_collision_to_walk_grid(collision)
                screen_image = env.screen_image()
                if hasattr(screen_image, "copy"):
                    screen_image = screen_image.copy()
                screenshot = self._png_payload(screen_image)
                overlay_image = render_collision_overlay(screen_image, collision, player_position=state.position)
                screenshot_overlay = self._png_payload(
                    overlay_image,
                    extra=overlay_metadata(overlay_image, player_position=state.position),
                )
                observation = {
                    "ram_watch": format_ram_watch(env.memory, state),
                    "game_area": game_area,
                    "game_area_collision": collision,
                    "walk_area_collision": walk_area_collision,
                    "player_screen_tile": grid_point_dict(PLAYER_SCREEN_TILE),
                    "player_walk_cell": grid_point_dict(PLAYER_WALK_CELL),
                    "screenshot": screenshot,
                    "screenshot_overlay": screenshot_overlay,
                }
                ui_payload = {
                    "screen_image": screen_image,
                    "overlay_image": overlay_image,
                    "ram_text": observation["ram_watch"],
                    "game_area_text": format_game_area_watch(env),
                    "collision_text": format_game_area_collision_watch(env),
                }
                self._next_visual_snapshot_at = time.monotonic() + 1.0 / self.snapshot_refresh_hz
            else:
                observation = dict(previous)
                walk_area_collision = observation.get("walk_area_collision", [])
                ui_payload = dict(previous_ui or {})

            observation.update(
                {
                    "tool_step_index": self.tool_step_index,
                    "frame_index": self.frame_index,
                    "state": state_dict,
                    "state_events": state_events,
                    "ram": state.raw,
                    "visible_world_cells": _visible_world_cells(state.position, walk_area_collision),
                    "safe_neighbor_world_cells": _safe_neighbor_world_cells(state.position, walk_area_collision),
                    "control_ui": self.control_panel is not None,
                }
            )
            observation["world_map"] = self.world_map_tracker.update_from_observation(observation)
            ui_payload["state"] = state
            ui_payload["world_map_text"] = self.world_map_tracker.current_ascii()

        with self._snapshot_condition:
            for event in state_events:
                _append_pending_state_event(self._pending_state_events, event)
            self._latest_observation = observation
            self._latest_ui_payload = ui_payload
            listeners = tuple(self._observation_listeners)
            self._snapshot_condition.notify_all()
        with self._frame_condition:
            self._frame_condition.notify_all()
        for listener in listeners:
            try:
                listener(observation)
            except Exception:
                continue

    def press_buttons(self, buttons: list[str]) -> dict[str, Any]:
        if not isinstance(buttons, list):
            raise ValueError("press_buttons expects a list of buttons")
        if not buttons:
            raise ValueError("press_buttons requires at least one button")
        if len(buttons) > MAX_BUTTONS_PER_ACTION:
            raise ValueError(f"press_buttons accepts at most {MAX_BUTTONS_PER_ACTION} buttons")

        validated = [str(button).strip().lower() for button in buttons]
        if any(token not in VALID_BUTTON_TOKENS for token in validated):
            invalid = next(token for token in validated if token not in VALID_BUTTON_TOKENS)
            raise ValueError(f"unknown button: {invalid}")

        with self._action_gate.turn():
            self._ensure_realtime_ticking()
            started_at = time.monotonic()
            executed_actions: list[dict[str, Any]] = []
            stop_reason = "buttons_complete"
            for token in validated:
                with self._env_lock:
                    start_frame = self.frame_index
                if token != "wait":
                    try:
                        self._submit_emulator_command("button", token, BUTTON_HOLD_FRAMES)
                    except RuntimeError:
                        if self._realtime_error:
                            stop_reason = self._realtime_stop_reason()
                            break
                        raise
                    completed = self._wait_realtime_frames(
                        BUTTON_HOLD_FRAMES + INPUT_AFTER_FRAMES,
                        start_frame=start_frame,
                    )
                else:
                    completed = self._wait_realtime(ACTION_WAIT_SECONDS, start_frame=start_frame)
                if not completed:
                    stop_reason = self._realtime_stop_reason()
                    break
                executed_actions.append({"button": token})
            self.tool_step_index += 1
            return {
                "requested_buttons": validated,
                "executed_actions": executed_actions,
                "steps_taken": len(executed_actions),
                "stop_reason": stop_reason,
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                "after_observation": self.observe(refresh_control_panel=False),
            }

    def wait(self) -> dict[str, Any]:
        with self._action_gate.turn():
            self._ensure_realtime_ticking()
            started_at = time.monotonic()
            with self._env_lock:
                start_frame = self.frame_index
            completed = self._wait_realtime(ACTION_WAIT_SECONDS, start_frame=start_frame)
            self.tool_step_index += 1
            return {
                "waited": completed,
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                "stop_reason": "wait_complete" if completed else self._realtime_stop_reason(),
                "after_observation": self.observe(refresh_control_panel=False),
            }

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        with self._action_gate.turn():
            state_path = self._resolve_state_path(kind, path, for_save=True)
            self._submit_emulator_command("save_state", state_path)
            self._notify_control_panel_saved(state_path)
            return {"saved": True, "kind": kind, "path": str(state_path)}

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        with self._action_gate.turn():
            state_path = self._resolve_state_path(kind, path, for_save=False)
            if not state_path.exists():
                raise FileNotFoundError(f"state not found: {state_path}")
            self._submit_emulator_command("load_state", state_path)
            with self._frame_condition:
                self._next_realtime_tick_at = time.monotonic()
            self._notify_control_panel_loaded(state_path)
            self.tool_step_index += 1
            return {
                "loaded": True,
                "kind": kind,
                "path": str(state_path),
                "after_observation": self.observe(refresh_control_panel=False),
            }

    def reset_to_fixed(self) -> dict[str, Any]:
        return self.load_state(kind="fixed")

    def move_to_world_cell(
        self,
        target_x: int,
        target_y: int,
    ) -> dict[str, Any]:
        target_x = _bounded_int(target_x, minimum=0, maximum=255, name="target_x")
        target_y = _bounded_int(target_y, minimum=0, maximum=255, name="target_y")
        with self._action_gate.turn():
            self._ensure_realtime_ticking()
            with self._snapshot_condition:
                cached_frame = int((self._latest_observation or {}).get("frame_index", -1))
            self._wait_for_snapshot_after(cached_frame)
            current_observation = self.observe(refresh_control_panel=False)
            current_position = _position_from_observation(current_observation)
            if current_position is None:
                raise ValueError("current player position is unknown")
            requested_world_cell = type(PLAYER_WALK_CELL)(target_x, target_y)
            initial_map_id = _map_id_from_observation(current_observation)
            target_out_of_visible_area = False
            executed_actions: list[dict[str, Any]] = []
            planned_path: list[dict[str, int]] = []
            navigation_segments: list[dict[str, Any]] = []
            resolved_world_cell: GridPoint | None = None
            stop_reason = "no_path"

            for segment_index in range(MAX_WORLD_NAVIGATION_SEGMENTS):
                segment_before = self.observe(refresh_control_panel=False)
                segment_position = _position_from_observation(segment_before)
                if segment_position is None:
                    stop_reason = "position_unknown"
                    break
                if _map_changed(initial_map_id, segment_before):
                    stop_reason = "interrupted_map_change"
                    break
                if _same_position(segment_position, requested_world_cell):
                    resolved_world_cell = requested_world_cell
                    stop_reason = "target_reached"
                    break

                interruption = _interruption_reason(segment_before)
                if interruption is not None:
                    stop_reason = interruption
                    break

                player_position = GridPoint(segment_position.x, segment_position.y)
                requested_walk_cell = map_position_to_walk_cell(requested_world_cell, player_position)
                segment_target_is_remote = not _walk_cell_in_visible_area(requested_walk_cell)
                target_out_of_visible_area = target_out_of_visible_area or segment_target_is_remote
                target_walk_cell = (
                    _clamp_walk_cell_to_visible_area(requested_walk_cell)
                    if segment_target_is_remote
                    else requested_walk_cell
                )

                segment_result = self._move_to_walk_cell(target_walk_cell.x, target_walk_cell.y)
                segment_after = segment_result.get("after_observation", {})
                after_position = _position_from_observation(segment_after)
                segment_resolved_world = _resolved_world_cell(segment_result, player_position)
                if segment_resolved_world is not None:
                    resolved_world_cell = segment_resolved_world
                segment_world_path = _world_path_for_segment(segment_result, player_position)
                _extend_unique_path(planned_path, segment_world_path)
                segment_actions = [
                    dict(action)
                    for action in segment_result.get("executed_actions", [])
                    if isinstance(action, dict)
                ]
                executed_actions.extend(segment_actions)
                segment_stop_reason = str(segment_result.get("stop_reason") or "no_path")
                made_progress = not _same_position(segment_position, after_position)
                navigation_segments.append(
                    {
                        "index": segment_index,
                        "from": grid_point_dict(GridPoint(segment_position.x, segment_position.y)),
                        "toward": grid_point_dict(
                            segment_resolved_world if segment_resolved_world is not None else player_position
                        ),
                        "to": None
                        if after_position is None
                        else {"x": int(after_position.x), "y": int(after_position.y)},
                        "steps_taken": len(segment_actions),
                        "stop_reason": segment_stop_reason,
                    }
                )

                if _map_changed(initial_map_id, segment_after):
                    stop_reason = "interrupted_map_change"
                    break
                if _same_position(after_position, requested_world_cell):
                    resolved_world_cell = requested_world_cell
                    stop_reason = "target_reached"
                    break

                if not segment_target_is_remote and segment_stop_reason == "target_reached":
                    stop_reason = "target_reached"
                    break

                interruption = _interruption_reason(segment_after)
                if interruption is not None:
                    stop_reason = interruption
                    break
                if not made_progress:
                    stop_reason = "no_path" if segment_stop_reason == "target_reached" else segment_stop_reason
                    break
                if segment_stop_reason not in {
                    "target_reached",
                    "planned_path_exhausted",
                    "max_steps_reached",
                }:
                    stop_reason = segment_stop_reason
                    break
            else:
                stop_reason = "navigation_limit_reached"

            after_observation = self.observe(refresh_control_panel=False)
            after_position = _position_from_observation(after_observation)
            requested_target_reached = _same_position(after_position, requested_world_cell)
            resolved_target_reached = (
                resolved_world_cell is not None and _same_position(after_position, resolved_world_cell)
            )
            if requested_target_reached:
                stop_reason = "target_reached"

            result = {
                "requested_world_cell": {"x": target_x, "y": target_y},
                "resolved_world_cell": None
                if resolved_world_cell is None
                else grid_point_dict(resolved_world_cell),
                "target_out_of_visible_area": target_out_of_visible_area,
                "requested_target_reached": requested_target_reached,
                "resolved_target_reached": resolved_target_reached,
                "planned_path": planned_path,
                "executed_actions": executed_actions,
                "steps_taken": len(executed_actions),
                "navigation_segments": navigation_segments,
                "navigation_replans": max(0, len(navigation_segments) - 1),
                "stop_reason": stop_reason,
                "before_observation": current_observation,
                "after_observation": after_observation,
            }
            return result

    def _move_to_walk_cell(self, target_x: int, target_y: int) -> dict[str, Any]:
        target_x = _bounded_int(target_x, minimum=0, maximum=9, name="target_x")
        target_y = _bounded_int(target_y, minimum=0, maximum=8, name="target_y")
        screen_tile = walk_cell_to_screen_tile(type(PLAYER_WALK_CELL)(target_x, target_y))
        result = self._move_to_screen_target(
            target_x=screen_tile.x,
            target_y=screen_tile.y,
        )
        return result

    def _move_to_screen_target(
        self,
        target_x: int,
        target_y: int,
    ) -> dict[str, Any]:
        self._navigation_attempt_index += 1
        before = self.observe(refresh_control_panel=False)
        plan = plan_screen_path(
            target_x,
            target_y,
            before["game_area_collision"],
            start=PLAYER_WALK_CELL,
            accept_nearest=True,
            blocked_edges=self._screen_blocked_edges(before),
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
        initial_map_id = _map_id_from_observation(before)

        world_goal = _world_goal_from_observation(before, plan.resolved_walk_cell)
        if stop_reason == "path_found":
            directions = directions_from_path(plan.path)
            stop_reason = "target_reached" if not directions else "planned_path_exhausted"

            for direction in directions[:MAX_MOVE_PATH_STEPS]:
                current = self.observe(refresh_control_panel=False)
                if _map_changed(initial_map_id, current):
                    stop_reason = "interrupted_map_change"
                    break
                if world_goal is not None and _position_from_observation(current) == world_goal:
                    stop_reason = "target_reached"
                    break

                interruption = _interruption_reason(current)
                if interruption is not None:
                    stop_reason = interruption
                    break

                before_step_position = _position_from_observation(current)
                with self._env_lock:
                    start_frame = self.frame_index
                try:
                    self._submit_emulator_command("button", direction, BUTTON_HOLD_FRAMES)
                except RuntimeError:
                    if self._realtime_error:
                        stop_reason = self._realtime_stop_reason()
                        break
                    raise
                step_stop_reason = self._wait_for_move_step(
                    before_step_position,
                    start_frame=start_frame,
                )
                executed_actions.append({"button": direction})
                self.tool_step_index += 1

                if step_stop_reason is None:
                    with self._env_lock:
                        current_frame = self.frame_index
                    remaining_frames = max(
                        0,
                        BUTTON_HOLD_FRAMES + INPUT_AFTER_FRAMES - (current_frame - start_frame),
                    )
                    if not self._wait_realtime_frames(remaining_frames, start_frame=current_frame):
                        stop_reason = self._realtime_stop_reason()
                        break

                after_step = self.observe(refresh_control_panel=False)
                after_step_position = _position_from_observation(after_step)
                if _map_changed(initial_map_id, after_step):
                    stop_reason = "interrupted_map_change"
                    break
                if world_goal is not None and after_step_position == world_goal:
                    stop_reason = "target_reached"
                    break
                interruption = _interruption_reason(after_step)
                if interruption is not None:
                    stop_reason = interruption
                    break
                if step_stop_reason is not None:
                    stop_reason = step_stop_reason
                    if stop_reason == "movement_blocked":
                        self._record_blocked_world_edge(current, direction)
                    break
                if before_step_position is not None and after_step_position == before_step_position:
                    if _controls_locked(after_step):
                        stop_reason = "controls_locked"
                    else:
                        self._record_blocked_world_edge(current, direction)
                        stop_reason = "movement_blocked"
                    break
            else:
                if len(executed_actions) < len(directions):
                    stop_reason = "max_steps_reached"
                elif world_goal is None:
                    stop_reason = "planned_path_exhausted"

        after = self.observe(refresh_control_panel=False)
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

    def _wait_for_move_step(self, before_position: GridPoint | None, *, start_frame: int) -> str | None:
        deadline = time.monotonic() + MOVE_STEP_TIMEOUT_SECONDS
        last_controls_locked = False
        with self._snapshot_condition:
            while True:
                if self._closing or self.env is None or self._realtime_error or not self.realtime_ticks_enabled:
                    return self._realtime_stop_reason()
                compact_observation = self._latest_observation or {}
                state_dict = compact_observation.get("state", {})
                interruption = _interruption_reason(compact_observation)
                if interruption is not None:
                    return interruption
                position = state_dict.get("position") if isinstance(state_dict, dict) else None
                current_position = None if not isinstance(position, dict) else GridPoint(
                    int(position["x"]),
                    int(position["y"]),
                )
                last_controls_locked = _controls_locked(compact_observation)
                if (
                    before_position is not None
                    and current_position != before_position
                    and int(compact_observation.get("frame_index", 0)) > start_frame
                ):
                    return None
                now = time.monotonic()
                if now >= deadline:
                    return "controls_locked" if last_controls_locked else "movement_blocked"
                self._snapshot_condition.wait(timeout=min(0.05, deadline - now))

    def _screen_blocked_edges(self, observation: dict[str, Any]) -> set[tuple[GridPoint, GridPoint]]:
        state = observation.get("state", {})
        map_id = state.get("map_id")
        position = _position_from_observation(observation)
        if map_id is None or position is None:
            return set()

        player = GridPoint(position.x, position.y)
        blocked: set[tuple[GridPoint, GridPoint]] = set()
        map_edges = self._blocked_world_edges.get(int(map_id), {})
        expired = [edge for edge, recorded_at in map_edges.items() if self._navigation_attempt_index - recorded_at > 8]
        for edge in expired:
            map_edges.pop(edge, None)
        for world_from, world_to in map_edges:
            screen_from = map_position_to_walk_cell(GridPoint(*world_from), player)
            screen_to = map_position_to_walk_cell(GridPoint(*world_to), player)
            if _walk_cell_in_visible_area(screen_from) and _walk_cell_in_visible_area(screen_to):
                blocked.add((screen_from, screen_to))
        return blocked

    def _record_blocked_world_edge(self, observation: dict[str, Any], direction: str) -> None:
        state = observation.get("state", {})
        map_id = state.get("map_id")
        position = _position_from_observation(observation)
        delta = {
            "right": (1, 0),
            "left": (-1, 0),
            "down": (0, 1),
            "up": (0, -1),
        }.get(direction)
        if map_id is None or position is None or delta is None:
            return
        source = (position.x, position.y)
        target = (position.x + delta[0], position.y + delta[1])
        self._blocked_world_edges.setdefault(int(map_id), {})[(source, target)] = self._navigation_attempt_index

    def _tick(self, frames: int, render: bool = False) -> bool:
        with self._frame_condition:
            env = self._require_env()
            running = env.tick(frames, render=render or self._should_render_ticks())
            self.frame_index += frames
            self._frame_condition.notify_all()
            return running

    def _should_render_ticks(self) -> bool:
        return True

    def _png_payload(self, image: Any, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        width, height = image.size
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        payload = {
            "format": "png",
            "width": int(width),
            "height": int(height),
            "base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        }
        if extra:
            payload.update(extra)
        return payload

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
            state_name = _safe_name(self.observe(refresh_control_panel=False)["state"]["map_name"])
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
        if self.control_panel is None:
            return

        try:
            now = time.monotonic()
            update_details = force or now - self._last_control_panel_update >= 1.0
            with self._snapshot_condition:
                payload = dict(self._latest_ui_payload or {})
            state = payload.get("state")
            screen_image = payload.get("screen_image")
            if state is None or screen_image is None:
                return
            self.control_panel.update_screen_image(screen_image)

            if update_details:
                self.control_panel.update_ram_text(str(payload.get("ram_text", "")))
                self.control_panel.update_game_area_text(str(payload.get("game_area_text", "")))
                self.control_panel.update_collision_text(str(payload.get("collision_text", "")))
                self.control_panel.update_overlay_image(payload.get("overlay_image"))
                self.control_panel.update_world_map_text(str(payload.get("world_map_text", "")))
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
                self.save_state(path=str(command.path))
            elif command.action == "load_state":
                if command.path is None:
                    continue
                if not command.path.exists():
                    self._notify_control_panel_error(f"not found: {command.path}")
                    continue
                self.load_state(path=str(command.path))
            elif command.action == "move":
                if command.target is None:
                    self._notify_control_panel_error("move target is missing")
                    continue
                try:
                    result = self.move_to_world_cell(command.target[0], command.target[1])
                except Exception as exc:
                    self._notify_control_panel_error(f"move failed: {type(exc).__name__}: {exc}")
                    continue
                if self.control_panel is not None:
                    self.control_panel.notify_move_result(result)
            elif command.action == "buttons":
                try:
                    result = self.press_buttons(list(command.buttons))
                except Exception as exc:
                    self._notify_control_panel_error(f"buttons failed: {type(exc).__name__}: {exc}")
                    continue
                if self.control_panel is not None:
                    self.control_panel.notify_buttons_result(result)
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
    raw = dict(state.raw)
    last_map_id = _int_or_none(raw.get("last_map"))
    block_position = _position_dict(raw.get("player_x_block"), raw.get("player_y_block"))
    dialog_text = state.dialog_text
    payload = {
        "map_id": state.map_id,
        "map_name": state.map_name,
        "position": None if state.position is None else {"x": state.position.x, "y": state.position.y},
        "block_position": block_position,
        "facing": state.facing,
        "mode": state.mode.value if isinstance(state.mode, GameMode) else str(state.mode),
        "in_battle": state.in_battle,
        "dialog_open": state.dialog_open,
        "player_name": state.player_name,
        "rival_name": state.rival_name,
        "money": state.money,
        "coins": state.coins,
        "game_time": state.game_time,
        "tileset": state.tileset,
        "pokedex_caught": state.pokedex_caught,
        "badges": list(state.badges),
        "party": [member.__dict__ for member in state.party],
        "items": [item.__dict__ for item in state.items],
        "warps": [{"x": warp.x, "y": warp.y} for warp in state.warps],
        "dialog_text": state.dialog_text,
        "nearby_npcs": [npc.__dict__ for npc in state.nearby_npcs],
        "nearby_exits": [exit_observation.__dict__ for exit_observation in state.nearby_exits],
        "flags": dict(state.flags),
        "map": {
            "id": state.map_id,
            "name": state.map_name,
            "last_map_id": last_map_id,
            "last_map_name": None if last_map_id is None else _map_name(last_map_id),
            "width": _int_or_none(raw.get("map_width")),
            "height": _int_or_none(raw.get("map_height")),
            "tileset": state.tileset,
            "tileset_id": _int_or_none(raw.get("tileset")),
            "tileset_type": _int_or_none(raw.get("tileset_type")),
            "collision_ptr": _int_or_none(raw.get("collision_ptr")),
            "grass_tile": _int_or_none(raw.get("grass_tile")),
        },
        "position_detail": {
            "tile": None if state.position is None else {"x": state.position.x, "y": state.position.y},
            "block": block_position,
            "facing": state.facing,
        },
        "dialog": {
            "open": state.dialog_open,
            "text": dialog_text,
            "box_detected": bool(raw.get("dialog_box_detected")),
            "has_text": bool(dialog_text),
            "text_length": 0 if dialog_text is None else len(dialog_text),
        },
        "menu": {
            "active": state.mode == GameMode.INVENTORY,
            "selection": _int_or_none(raw.get("menu_selection")),
            "start_menu_cursor": _int_or_none(raw.get("start_menu_cursor")),
        },
        "counts": {
            "party": _int_or_none(raw.get("party_count")),
            "items": _int_or_none(raw.get("item_count")),
            "warps": _int_or_none(raw.get("warp_count")),
            "badges": len(state.badges),
            "pokedex_caught": state.pokedex_caught,
        },
        "raw": raw,
        "summary": state.summary(),
    }
    if state.in_battle:
        battle: dict[str, Any] = {
            "active": True,
            "type": _int_or_none(raw.get("battle_type")),
            "kind": _int_or_none(raw.get("battle_kind")),
            "turns": _int_or_none(raw.get("battle_turns")),
        }
        if state.battle_opponent is not None:
            battle["opponent"] = {
                "species": state.battle_opponent.species,
                "level": state.battle_opponent.level,
                "hp": state.battle_opponent.hp,
                "max_hp": state.battle_opponent.max_hp,
                "status": state.battle_opponent.status,
                "types": list(state.battle_opponent.types),
            }
        payload["battle"] = battle
    return payload


def _visible_world_cells(position: Position | None, walk_area_collision: list[list[int]]) -> list[list[dict[str, Any]]]:
    if position is None:
        return []
    player_position = type(PLAYER_WALK_CELL)(position.x, position.y)
    rows: list[list[dict[str, Any]]] = []
    for walk_y, row in enumerate(walk_area_collision):
        cells: list[dict[str, Any]] = []
        for walk_x, walkable in enumerate(row):
            world = walk_cell_to_map_position(type(PLAYER_WALK_CELL)(walk_x, walk_y), player_position)
            cells.append({"x": world.x, "y": world.y, "walkable": bool(walkable)})
        rows.append(cells)
    return rows


def _safe_neighbor_world_cells(position: Position | None, walk_area_collision: list[list[int]]) -> list[dict[str, Any]]:
    if position is None:
        return []
    player_position = type(PLAYER_WALK_CELL)(position.x, position.y)
    candidates = [
        ("right", 1, 0),
        ("down", 0, 1),
        ("left", -1, 0),
        ("up", 0, -1),
    ]
    targets: list[dict[str, Any]] = []
    for direction, dx, dy in candidates:
        walk_x = PLAYER_WALK_CELL.x + dx
        walk_y = PLAYER_WALK_CELL.y + dy
        if 0 <= walk_y < len(walk_area_collision) and 0 <= walk_x < len(walk_area_collision[walk_y]):
            if not walk_area_collision[walk_y][walk_x]:
                continue
            world = walk_cell_to_map_position(type(PLAYER_WALK_CELL)(walk_x, walk_y), player_position)
            targets.append({"direction": direction, "x": world.x, "y": world.y})
    return targets


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "map_id": state.get("map_id"),
        "map_name": state.get("map_name"),
        "position": state.get("position"),
        "mode": state.get("mode"),
        "in_battle": state.get("in_battle"),
        "dialog_open": state.get("dialog_open"),
        "dialog_text": state.get("dialog_text"),
        "money": state.get("money"),
        "coins": state.get("coins"),
        "badges": state.get("badges"),
        "party": state.get("party"),
        "items": state.get("items"),
        "pokedex_caught": state.get("pokedex_caught"),
        "flags": _durable_flags(state.get("flags")),
        "warps": state.get("warps"),
    }


def _state_events(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    if previous is None:
        return [{"type": "initial_observation", "summary": current.get("summary")}]

    events: list[dict[str, Any]] = []
    _append_changed_event(events, "mode_changed", previous, current, "mode")

    if previous.get("map_id") != current.get("map_id"):
        events.append(
            {
                "type": "map_changed",
                "from": {"id": previous.get("map_id"), "name": previous.get("map_name")},
                "to": {"id": current.get("map_id"), "name": current.get("map_name")},
            }
        )
        events.append(
            {
                "type": "warp",
                "from": {"id": previous.get("map_id"), "name": previous.get("map_name")},
                "to": {"id": current.get("map_id"), "name": current.get("map_name")},
            }
        )

    if previous.get("position") != current.get("position"):
        events.append({"type": "position_changed", "from": previous.get("position"), "to": current.get("position")})

    previous_dialog_open = bool(previous.get("dialog_open"))
    current_dialog_open = bool(current.get("dialog_open"))
    if not previous_dialog_open and current_dialog_open:
        events.append({"type": "dialog_opened", "text": current.get("dialog_text")})
    elif previous_dialog_open and not current_dialog_open:
        events.append({"type": "dialog_closed", "previous_text": previous.get("dialog_text")})
    elif current_dialog_open and previous.get("dialog_text") != current.get("dialog_text"):
        events.append(
            {
                "type": "dialog_text_changed",
                "from": previous.get("dialog_text"),
                "to": current.get("dialog_text"),
            }
        )

    if not bool(previous.get("in_battle")) and bool(current.get("in_battle")):
        events.append({"type": "battle_started"})
    elif bool(previous.get("in_battle")) and not bool(current.get("in_battle")):
        events.append({"type": "battle_ended"})

    previous_menu = previous.get("mode") == "inventory"
    current_menu = current.get("mode") == "inventory"
    if not previous_menu and current_menu:
        events.append({"type": "menu_opened"})
    elif previous_menu and not current_menu:
        events.append({"type": "menu_closed"})

    item_deltas = _positive_item_deltas(previous.get("items"), current.get("items"))
    for item_name, quantity in item_deltas.items():
        events.append({"type": "item_obtained", "item": item_name, "quantity": quantity})
    previous_party = previous.get("party") if isinstance(previous.get("party"), list) else []
    current_party = current.get("party") if isinstance(current.get("party"), list) else []
    if len(current_party) > len(previous_party):
        events.append({"type": "pokemon_obtained", "party_size": len(current_party)})

    for key, event_type in (
        ("money", "money_changed"),
        ("coins", "coins_changed"),
        ("badges", "badges_changed"),
        ("party", "party_changed"),
        ("items", "items_changed"),
        ("pokedex_caught", "pokedex_changed"),
        ("warps", "warps_changed"),
    ):
        _append_changed_event(events, event_type, previous, current, key)
    previous_flags = _durable_flags(previous.get("flags"))
    current_flags = _durable_flags(current.get("flags"))
    if previous_flags != current_flags:
        events.append({"type": "event_flags_changed", "from": previous_flags, "to": current_flags})

    return events


def _append_pending_state_event(
    pending: deque[dict[str, Any]],
    event: dict[str, Any],
) -> None:
    incoming = dict(event)
    if incoming.get("type") == "dialog_text_changed":
        for existing in reversed(pending):
            existing_type = existing.get("type")
            if existing_type == "dialog_closed":
                break
            if existing_type == "dialog_opened":
                existing["text"] = incoming.get("to")
                return
            if existing_type == "dialog_text_changed":
                existing["to"] = incoming.get("to")
                return
    pending.append(incoming)


def _append_changed_event(
    events: list[dict[str, Any]],
    event_type: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    key: str,
) -> None:
    if previous.get(key) != current.get(key):
        events.append({"type": event_type, "from": previous.get(key), "to": current.get(key)})


def _positive_item_deltas(previous_items: Any, current_items: Any) -> dict[str, int]:
    def counts(items: Any) -> dict[str, int]:
        result: dict[str, int] = {}
        if not isinstance(items, list):
            return result
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("item_id") or "unknown")
            result[name] = result.get(name, 0) + int(item.get("quantity") or 0)
        return result

    before = counts(previous_items)
    after = counts(current_items)
    return {name: quantity - before.get(name, 0) for name, quantity in after.items() if quantity > before.get(name, 0)}


def _durable_flags(flags: Any) -> dict[str, Any]:
    if not isinstance(flags, dict):
        return {}
    return {str(key): value for key, value in flags.items() if not str(key).startswith("has_")}


def _position_dict(x: Any, y: Any) -> dict[str, int] | None:
    x_value = _int_or_none(x)
    y_value = _int_or_none(y)
    if x_value is None or y_value is None:
        return None
    return {"x": x_value, "y": y_value}


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_name(map_id: int) -> str:
    from pokemon_agent.memory.memory_reader import POKEMON_RED_MAP_NAMES

    return POKEMON_RED_MAP_NAMES.get(map_id, f"Map {map_id:#04x}")


def _walk_cell_in_visible_area(point: Any) -> bool:
    return 0 <= int(point.x) <= 9 and 0 <= int(point.y) <= 8


def _clamp_walk_cell_to_visible_area(point: Any) -> Any:
    return type(PLAYER_WALK_CELL)(
        max(0, min(9, int(point.x))),
        max(0, min(8, int(point.y))),
    )


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


def _same_position(left: Any, right: Any) -> bool:
    return bool(
        left is not None
        and right is not None
        and int(left.x) == int(right.x)
        and int(left.y) == int(right.y)
    )


def _map_id_from_observation(observation: dict[str, Any]) -> int | None:
    return _int_or_none(observation.get("state", {}).get("map_id"))


def _map_changed(initial_map_id: int | None, observation: dict[str, Any]) -> bool:
    current_map_id = _map_id_from_observation(observation)
    return initial_map_id is not None and current_map_id is not None and current_map_id != initial_map_id


def _resolved_world_cell(result: dict[str, Any], player_position: GridPoint) -> GridPoint | None:
    resolved_walk = result.get("resolved_target", {}).get("walk_cell")
    if not isinstance(resolved_walk, dict):
        return None
    return walk_cell_to_map_position(
        GridPoint(int(resolved_walk["x"]), int(resolved_walk["y"])),
        player_position,
    )


def _world_path_for_segment(result: dict[str, Any], player_position: GridPoint) -> list[dict[str, int]]:
    return [
        grid_point_dict(
            walk_cell_to_map_position(
                GridPoint(int(point["x"]), int(point["y"])),
                player_position,
            )
        )
        for point in result.get("planned_path", [])
        if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None
    ]


def _extend_unique_path(path: list[dict[str, int]], segment: list[dict[str, int]]) -> None:
    for point in segment:
        if not path or path[-1] != point:
            path.append(point)


def _world_goal_from_observation(observation: dict[str, Any], target_walk_cell: Any) -> Position | None:
    position = _position_from_observation(observation)
    if position is None:
        return None
    map_position = walk_cell_to_map_position(target_walk_cell, type(PLAYER_WALK_CELL)(position.x, position.y))
    return Position(x=map_position.x, y=map_position.y)


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


def _controls_locked(observation: dict[str, Any]) -> bool:
    raw = observation.get("state", {}).get("raw", {})
    return bool(raw.get("controls_locked")) if isinstance(raw, dict) else False

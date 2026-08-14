from __future__ import annotations

import argparse
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pokemon_agent.emulator.pyboy_env import PyBoyEnvironment
from pokemon_agent.memory.memory_reader import PokemonRedMemoryReader
from pokemon_agent.memory.ram_map import format_ram_watch
from pokemon_agent.memory.world_state import GameMode, GameState
from pokemon_agent.tools.pathfinding import directions_from_path
from pokemon_agent.tools.screen_navigation import (
    PLAYER_WALK_CELL,
    grid_point_dict,
    map_position_to_walk_cell,
    plan_screen_path,
    walk_cell_to_map_position,
    walk_cell_to_screen_tile,
)
from pokemon_agent.ui.control_panel import ControlCommand, QtStateControlPanel
from pokemon_agent.vision.capture import CaptureConfig, CaptureRecorder
from pokemon_agent.vision.game_area import format_game_area_collision_watch, format_game_area_watch
from pokemon_agent.vision.overlay import render_collision_overlay

def run_rom(
    rom: Path,
    steps: int,
    window: str,
    render: bool,
    capture_config: CaptureConfig,
    state_dir: Path,
    load_state: Path | None,
    save_final: Path | None,
    save_every: int,
    tick_frames: int,
    control_panel: QtStateControlPanel | None = None,
) -> None:
    env = PyBoyEnvironment(rom_path=rom, window=window)
    reader = PokemonRedMemoryReader()
    capture = CaptureRecorder(env, capture_config)
    last_ram_update = 0.0
    last_screen_update = 0.0
    screen_refresh_interval = 1.0 / 30.0
    control_frame_index = 0
    queued_control_input: _QueuedControlInput | None = None

    try:
        if load_state is not None:
            env.load_state(load_state)
            logging.info("loaded state=%s", load_state)

        for index in range(steps):
            state = reader.read(env.memory)
            if index % 60 == 0:
                logging.info("frame=%s state=%s", index, state.summary())
            if not env.tick(tick_frames, render=render):
                logging.info("emulator requested stop")
                break
            control_frame_index += max(tick_frames, 0)
            capture.maybe_capture(index, state)

            if save_every > 0 and index > 0 and index % save_every == 0:
                path = _save_state_path(state_dir, "autosave", index, state)
                env.save_state(path)
                logging.info("saved state=%s", path)

            if control_panel is not None:
                now = time.monotonic()
                screen_image = None
                if now - last_screen_update >= screen_refresh_interval:
                    screen_image = env.screen_image()
                    control_panel.update_screen_image(screen_image)
                    last_screen_update = now
                if now - last_ram_update >= 1.0:
                    if screen_image is None:
                        screen_image = env.screen_image()
                    control_panel.update_ram_text(format_ram_watch(env.memory, state))
                    control_panel.update_game_area_text(format_game_area_watch(env))
                    control_panel.update_collision_text(format_game_area_collision_watch(env))
                    control_panel.update_overlay_image(
                        render_collision_overlay(screen_image, env.game_area_collision(), player_position=state.position)
                    )
                    last_ram_update = now

            if control_panel is not None:
                stop_requested, new_input = _handle_control_commands(
                    env,
                    control_panel,
                    control_panel.poll(state),
                    current_state=state,
                    current_frame=control_frame_index,
                )
                if new_input is not None:
                    queued_control_input = new_input
                queued_control_input = _pump_control_input_queue(
                    env,
                    control_panel,
                    queued_control_input,
                    current_state=state,
                    current_frame=control_frame_index,
                )
                if stop_requested:
                    logging.info("control panel requested stop")
                    break
    finally:
        if save_final is not None:
            env.save_state(save_final)
            logging.info("saved final state=%s", save_final)
        if control_panel is not None:
            control_panel.close()
        capture.close()
        env.stop(save=False)


@dataclass
class _QueuedControlInput:
    kind: str
    result: dict[str, Any]
    tokens: list[str]
    next_frame: int
    index: int = 0
    button_frames: int = 4
    after_frames: int = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pokemon Red PyBoy player and control UI.")
    parser.add_argument("--rom", type=Path, help="Path to a legally obtained Pokemon Red ROM.")
    parser.add_argument("--steps", type=int, default=300, help="Number of emulator loop steps to execute.")
    parser.add_argument("--window", default="null", help="PyBoy window backend, for example SDL2 or null.")
    parser.add_argument("--render", action="store_true", help="Render the last frame of each tick batch.")
    parser.add_argument("--tick-frames", type=int, default=1, help="Frames to advance per emulator loop.")
    parser.add_argument("--state-dir", type=Path, default=Path("states"), help="Directory for generated save-state files.")
    parser.add_argument("--load-state", type=Path, help="Load a PyBoy save-state file before starting.")
    parser.add_argument("--save-final", type=Path, help="Save a PyBoy state to this path before exiting.")
    parser.add_argument("--save-every", type=int, default=0, help="Autosave a PyBoy state every N loop steps.")
    parser.add_argument("--capture-dir", type=Path, default=Path("captures"), help="Directory for captured frames.")
    parser.add_argument("--screenshot-every", type=int, default=0, help="Save a PNG every N loop steps.")
    parser.add_argument("--record-gif", type=Path, help="Save an animated GIF of the run.")
    parser.add_argument("--record-mp4", type=Path, help="Save an MP4 video using ffmpeg if available.")
    parser.add_argument("--video-every", type=int, default=1, help="Capture one video frame every N agent steps.")
    parser.add_argument("--video-fps", type=int, default=30, help="Playback FPS for GIF/MP4 output.")
    parser.add_argument("--keep-video-frames", action="store_true", help="Keep PNG frames used to encode MP4.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    if args.rom is None:
        raise SystemExit("Provide --rom.")

    capture_config = CaptureConfig(
        directory=args.capture_dir,
        screenshot_every=args.screenshot_every,
        record_gif=args.record_gif,
        record_mp4=args.record_mp4,
        video_every=args.video_every,
        video_fps=args.video_fps,
        keep_video_frames=args.keep_video_frames,
    )
    run_rom(
        args.rom,
        steps=args.steps,
        window=args.window,
        render=args.render,
        capture_config=capture_config,
        state_dir=args.state_dir,
        load_state=args.load_state,
        save_final=args.save_final,
        save_every=args.save_every,
        tick_frames=args.tick_frames,
    )


def _handle_control_commands(
    env: PyBoyEnvironment,
    control_panel: QtStateControlPanel,
    commands: list[ControlCommand],
    *,
    current_state: GameState | None = None,
    current_frame: int = 0,
) -> tuple[bool, _QueuedControlInput | None]:
    stop_requested = False
    queued_input: _QueuedControlInput | None = None
    for command in commands:
        if command.action == "save_state":
            if command.path is None:
                continue
            env.save_state(command.path)
            control_panel.notify_saved(command.path)
            logging.info("saved state=%s", command.path)
        elif command.action == "load_state":
            if command.path is None:
                continue
            if not command.path.exists():
                control_panel.notify_error(f"not found: {command.path}")
                logging.warning("state not found=%s", command.path)
                continue
            env.load_state(command.path)
            control_panel.notify_loaded(command.path)
            logging.info("loaded state=%s", command.path)
        elif command.action == "move":
            try:
                result, planned_input = _plan_control_move_from_command(
                    env,
                    command,
                    current_state=current_state,
                    current_frame=current_frame,
                )
            except Exception as exc:
                control_panel.notify_error(f"move failed: {type(exc).__name__}: {exc}")
                logging.warning("move command failed: %s", exc)
                continue
            if planned_input is None:
                control_panel.notify_move_result(result)
            else:
                queued_input = planned_input
            logging.info(
                "move queued world_target=%s resolved_world=%s stop_reason=%s queued_steps=%s planned_actions=%s",
                result.get("requested_world_cell"),
                result.get("resolved_world_cell"),
                result.get("stop_reason"),
                result.get("queued_steps"),
                [action.get("button") for action in result.get("planned_actions", [])],
            )
        elif command.action == "buttons":
            try:
                result, planned_input = _plan_control_buttons_from_command(command, current_frame=current_frame)
            except Exception as exc:
                control_panel.notify_error(f"buttons failed: {type(exc).__name__}: {exc}")
                logging.warning("buttons command failed: %s", exc)
                continue
            if planned_input is None:
                control_panel.notify_buttons_result(result)
            else:
                queued_input = planned_input
            logging.info(
                "buttons queued requested=%s stop_reason=%s queued_steps=%s planned_actions=%s",
                result.get("requested_buttons"),
                result.get("stop_reason"),
                result.get("queued_steps"),
                [action.get("button") for action in result.get("planned_actions", [])],
            )
        elif command.action == "stop":
            stop_requested = True
    return stop_requested, queued_input


def _plan_control_move_from_command(
    env: PyBoyEnvironment,
    command: ControlCommand,
    *,
    current_state: GameState | None,
    current_frame: int,
) -> tuple[dict[str, Any], _QueuedControlInput | None]:
    if command.target is None:
        raise ValueError("move command requires a target")

    target_x = _bounded_int(command.target[0], minimum=0, maximum=255, name="target_x")
    target_y = _bounded_int(command.target[1], minimum=0, maximum=255, name="target_y")
    max_steps = 8
    if current_state is None or current_state.position is None:
        raise ValueError("current player position is unknown")

    player_position = type(PLAYER_WALK_CELL)(current_state.position.x, current_state.position.y)
    requested_map_position = type(PLAYER_WALK_CELL)(target_x, target_y)
    target_walk_cell = map_position_to_walk_cell(requested_map_position, player_position)
    if not (0 <= target_walk_cell.x <= 9 and 0 <= target_walk_cell.y <= 8):
        raise ValueError(
            "target map coordinate is outside the current visible walk area: "
            f"map=({target_x}, {target_y}) player=({current_state.position.x}, {current_state.position.y}) "
            f"walk=({target_walk_cell.x}, {target_walk_cell.y})"
        )

    screen_tile = walk_cell_to_screen_tile(target_walk_cell)
    plan = plan_screen_path(
        screen_tile.x,
        screen_tile.y,
        env.game_area_collision(),
        start=PLAYER_WALK_CELL,
        accept_nearest=True,
    )
    executed_actions: list[dict[str, Any]] = []
    planned_actions: list[dict[str, Any]] = []
    queued_directions: list[str] = []
    stop_reason = plan.stop_reason
    planned_stop_reason = stop_reason

    interruption = _interruption_reason(current_state)
    if interruption is not None:
        stop_reason = interruption
    elif stop_reason == "path_found":
        directions = directions_from_path(plan.path)
        stop_reason = "target_reached" if not directions else "planned_path_exhausted"
        planned_stop_reason = stop_reason

        queued_directions = list(directions[:max_steps])
        planned_actions = [{"button": direction} for direction in queued_directions]
        if queued_directions:
            stop_reason = "queued_path"
            planned_stop_reason = "max_steps_reached" if len(queued_directions) < len(directions) else "planned_path_exhausted"

    resolved_map_position = walk_cell_to_map_position(plan.resolved_walk_cell, player_position)
    result = {
        "requested_world_cell": {"x": target_x, "y": target_y},
        "resolved_world_cell": grid_point_dict(resolved_map_position),
        "planned_path": [
            grid_point_dict(walk_cell_to_map_position(point, player_position))
            for point in plan.path
        ],
        "planned_actions": planned_actions,
        "queued_steps": len(queued_directions),
        "planned_stop_reason": planned_stop_reason,
        "executed_actions": executed_actions,
        "steps_taken": len(executed_actions),
        "stop_reason": stop_reason,
    }
    queued_move = None
    if queued_directions:
        queued_move = _QueuedControlInput(
            kind="move",
            result=result,
            tokens=queued_directions,
            next_frame=current_frame,
        )
    return result, queued_move


def _plan_control_buttons_from_command(
    command: ControlCommand,
    *,
    current_frame: int,
) -> tuple[dict[str, Any], _QueuedControlInput | None]:
    buttons = list(command.buttons)
    if not buttons:
        raise ValueError("buttons command requires at least one token")
    if len(buttons) > 16:
        raise ValueError("buttons command accepts at most 16 tokens")
    for button in buttons:
        if button not in {"a", "b", "start", "select", "left", "right", "up", "down", "wait"}:
            raise ValueError(f"invalid button token: {button}")

    planned_actions = [{"button": button} for button in buttons]
    result = {
        "requested_buttons": buttons,
        "planned_actions": planned_actions,
        "queued_steps": len(buttons),
        "executed_actions": [],
        "steps_taken": 0,
        "stop_reason": "queued_buttons",
    }
    return result, _QueuedControlInput(
        kind="buttons",
        result=result,
        tokens=buttons,
        next_frame=current_frame,
    )


def _pump_control_input_queue(
    env: PyBoyEnvironment,
    control_panel: QtStateControlPanel,
    queued_input: _QueuedControlInput | None,
    *,
    current_state: GameState | None,
    current_frame: int,
) -> _QueuedControlInput | None:
    if queued_input is None:
        return None

    if queued_input.kind == "move":
        interruption = _interruption_reason(current_state)
    else:
        interruption = None

    if interruption is not None:
        queued_input.result["stop_reason"] = interruption
        _notify_control_input_result(control_panel, queued_input)
        logging.info(
            "move interrupted stop_reason=%s steps=%s actions=%s",
            interruption,
            queued_input.result.get("steps_taken"),
            [action.get("button") for action in queued_input.result.get("executed_actions", [])],
        )
        return None

    if current_frame < queued_input.next_frame:
        return queued_input

    token = queued_input.tokens[queued_input.index]
    if token == "wait":
        action = {"button": "wait"}
        next_frame = current_frame + queued_input.after_frames
    else:
        env.button(token, frames=queued_input.button_frames)
        action = {"button": token}
        next_frame = current_frame + queued_input.button_frames + queued_input.after_frames

    queued_input.result.setdefault("executed_actions", []).append(action)
    queued_input.index += 1
    queued_input.result["steps_taken"] = queued_input.index
    queued_input.result["stop_reason"] = _queued_stop_reason(queued_input)
    _notify_control_input_result(control_panel, queued_input)
    logging.info(
        "%s async_press button=%s frame=%s next_frame=%s steps=%s/%s",
        queued_input.kind,
        token,
        current_frame,
        next_frame,
        queued_input.index,
        len(queued_input.tokens),
    )

    if queued_input.index >= len(queued_input.tokens):
        logging.info(
            "%s completed stop_reason=%s steps=%s actions=%s",
            queued_input.kind,
            queued_input.result.get("stop_reason"),
            queued_input.result.get("steps_taken"),
            [action.get("button") for action in queued_input.result.get("executed_actions", [])],
        )
        return None

    queued_input.next_frame = next_frame
    return queued_input


def _queued_stop_reason(queued_input: _QueuedControlInput) -> str:
    if queued_input.index < len(queued_input.tokens):
        return "queued_path" if queued_input.kind == "move" else "queued_buttons"
    if queued_input.kind == "move":
        return str(queued_input.result.get("planned_stop_reason", "planned_path_exhausted"))
    return "buttons_complete"


def _notify_control_input_result(control_panel: QtStateControlPanel, queued_input: _QueuedControlInput) -> None:
    if queued_input.kind == "move":
        control_panel.notify_move_result(queued_input.result)
    else:
        control_panel.notify_buttons_result(queued_input.result)


def _interruption_reason(state: GameState | None) -> str | None:
    if state is None:
        return None
    if state.in_battle:
        return "interrupted_battle"
    if state.dialog_open:
        return "interrupted_dialog"
    if state.mode == GameMode.INVENTORY:
        return "interrupted_menu"
    return None


def _bounded_int(value: int, *, minimum: int, maximum: int, name: str) -> int:
    try:
        coerced = int(value)
    except Exception as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if coerced < minimum or coerced > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return coerced


def _save_state_path(state_dir: Path, prefix: str, index: int, state: GameState) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    map_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", state.map_name.strip()).strip("_") or "unknown"
    return state_dir / f"{prefix}_{index:06d}_{timestamp}_{map_name}.state"


if __name__ == "__main__":
    main()

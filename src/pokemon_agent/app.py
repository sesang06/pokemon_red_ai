from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from pokemon_agent.agent.battle import RuleBasedBattleAgent
from pokemon_agent.agent.dialog import DialogAgent
from pokemon_agent.agent.inventory import InventoryAgent
from pokemon_agent.agent.navigator import NavigationAgent
from pokemon_agent.agent.planner import ScriptedPlanner
from pokemon_agent.agent.task_manager import TaskManager
from pokemon_agent.emulator.controller import ActionExecutor
from pokemon_agent.emulator.pyboy_env import PyBoyEnvironment
from pokemon_agent.memory.memory_reader import PokemonRedMemoryReader
from pokemon_agent.memory.ram_map import format_ram_watch
from pokemon_agent.memory.world_state import GameMode, GameState, Position
from pokemon_agent.ui.control_panel import ControlCommand, QtStateControlPanel
from pokemon_agent.vision.capture import CaptureConfig, CaptureRecorder
from pokemon_agent.vision.game_area import format_game_area_collision_watch, format_game_area_watch


def build_task_manager() -> TaskManager:
    return TaskManager(
        planner=ScriptedPlanner(),
        navigator=NavigationAgent(),
        battle=RuleBasedBattleAgent(),
        dialog=DialogAgent(),
        inventory=InventoryAgent(),
    )


def dry_run() -> None:
    state = GameState(
        map_id=0,
        map_name="Pallet Town",
        position=Position(x=5, y=6),
        mode=GameMode.EXPLORE,
    )
    manager = build_task_manager()
    decision = manager.decide(state)
    print("Dry-run state:")
    print(state.summary())
    print()
    print("First decision:")
    print(decision)


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
    manual_play: bool,
    tick_frames: int,
    control_panel: QtStateControlPanel | None = None,
) -> None:
    env = PyBoyEnvironment(rom_path=rom, window=window)
    reader = PokemonRedMemoryReader()
    executor = ActionExecutor(env)
    manager = build_task_manager()
    capture = CaptureRecorder(env, capture_config)
    last_ram_update = 0.0

    try:
        if load_state is not None:
            env.load_state(load_state)
            logging.info("loaded state=%s", load_state)

        for index in range(steps):
            state = reader.read(env.memory)
            if manual_play:
                if index % 60 == 0:
                    logging.info("frame=%s state=%s", index, state.summary())
                if not env.tick(tick_frames, render=render):
                    logging.info("emulator requested stop")
                    break
            else:
                decision = manager.decide(state)
                logging.info("step=%s state=%s decision=%s", index, state.summary(), decision)
                executor.execute(decision.actions)
                if not env.tick(decision.settle_frames, render=render):
                    logging.info("emulator requested stop")
                    break
            capture.maybe_capture(index, state)

            if save_every > 0 and index > 0 and index % save_every == 0:
                path = _save_state_path(state_dir, "autosave", index, state)
                env.save_state(path)
                logging.info("saved state=%s", path)

            if control_panel is not None:
                now = time.monotonic()
                if now - last_ram_update >= 1.0:
                    control_panel.update_ram_text(format_ram_watch(env.memory))
                    control_panel.update_game_area_text(format_game_area_watch(env))
                    control_panel.update_collision_text(format_game_area_collision_watch(env))
                    last_ram_update = now

            if control_panel is not None and _handle_control_commands(env, control_panel, control_panel.poll(state)):
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pokemon Red PyBoy agent scaffold.")
    parser.add_argument("--rom", type=Path, help="Path to a legally obtained Pokemon Red ROM.")
    parser.add_argument("--steps", type=int, default=300, help="Number of agent decisions to execute.")
    parser.add_argument("--window", default="null", help="PyBoy window backend, for example SDL2 or null.")
    parser.add_argument("--render", action="store_true", help="Render the last frame of each tick batch.")
    parser.add_argument("--manual-play", action="store_true", help="Do not run the agent; let the emulator window receive input.")
    parser.add_argument("--tick-frames", type=int, default=1, help="Frames to advance per loop in manual-play mode.")
    parser.add_argument("--state-dir", type=Path, default=Path("states"), help="Directory for generated save-state files.")
    parser.add_argument("--load-state", type=Path, help="Load a PyBoy save-state file before starting.")
    parser.add_argument("--save-final", type=Path, help="Save a PyBoy state to this path before exiting.")
    parser.add_argument("--save-every", type=int, default=0, help="Autosave a PyBoy state every N loop steps.")
    parser.add_argument("--capture-dir", type=Path, default=Path("captures"), help="Directory for captured frames.")
    parser.add_argument("--screenshot-every", type=int, default=0, help="Save a PNG every N agent steps.")
    parser.add_argument("--record-gif", type=Path, help="Save an animated GIF of the run.")
    parser.add_argument("--record-mp4", type=Path, help="Save an MP4 video using ffmpeg if available.")
    parser.add_argument("--video-every", type=int, default=1, help="Capture one video frame every N agent steps.")
    parser.add_argument("--video-fps", type=int, default=30, help="Playback FPS for GIF/MP4 output.")
    parser.add_argument("--keep-video-frames", action="store_true", help="Keep PNG frames used to encode MP4.")
    parser.add_argument("--dry-run", action="store_true", help="Run without a ROM and print one decision.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    if args.dry_run:
        dry_run()
        return

    if args.rom is None:
        raise SystemExit("Pass --dry-run or provide --rom.")

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
        manual_play=args.manual_play,
        tick_frames=args.tick_frames,
    )


def _handle_control_commands(
    env: PyBoyEnvironment,
    control_panel: QtStateControlPanel,
    commands: list[ControlCommand],
) -> bool:
    stop_requested = False
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
        elif command.action == "stop":
            stop_requested = True
    return stop_requested


def _save_state_path(state_dir: Path, prefix: str, index: int, state: GameState) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    map_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", state.map_name.strip()).strip("_") or "unknown"
    return state_dir / f"{prefix}_{index:06d}_{timestamp}_{map_name}.state"


if __name__ == "__main__":
    main()

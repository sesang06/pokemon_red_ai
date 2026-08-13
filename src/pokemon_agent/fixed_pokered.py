from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokemon_agent.app import run_rom
from pokemon_agent.emulator.pyboy_env import PyBoyEnvironment
from pokemon_agent.ui.control_panel import QtStateControlPanel
from pokemon_agent.vision.capture import CaptureConfig


@dataclass(frozen=True)
class FixedPokeredPaths:
    project_root: Path
    rom: Path
    fixed_state: Path
    pyboy_hotkey_state: Path


def default_paths() -> FixedPokeredPaths:
    source_root = Path(__file__).resolve().parents[2]
    project_root = Path.cwd() if (Path.cwd() / "src" / "pokered.gb").exists() else source_root
    rom = project_root / "src" / "pokered.gb"
    return FixedPokeredPaths(
        project_root=project_root,
        rom=rom,
        fixed_state=project_root / "states" / "fixed_start.state",
        pyboy_hotkey_state=rom.with_name(f"{rom.name}.state"),
    )


def ensure_fixed_state(paths: FixedPokeredPaths, force_boot_state: bool = False) -> Path:
    if not paths.rom.exists():
        raise SystemExit(f"ROM not found: {paths.rom}")

    paths.fixed_state.parent.mkdir(parents=True, exist_ok=True)

    if paths.fixed_state.exists() and not force_boot_state:
        return paths.fixed_state

    if paths.pyboy_hotkey_state.exists() and not force_boot_state:
        shutil.copy2(paths.pyboy_hotkey_state, paths.fixed_state)
        logging.info("fixed state imported from %s", paths.pyboy_hotkey_state)
        return paths.fixed_state

    env = PyBoyEnvironment(paths.rom, window="null")
    try:
        env.tick(1, render=False)
        env.save_state(paths.fixed_state)
        logging.info("fixed state created from boot state: %s", paths.fixed_state)
    finally:
        env.stop(save=False)

    return paths.fixed_state


def fix_current_state(paths: FixedPokeredPaths) -> Path:
    if not paths.pyboy_hotkey_state.exists():
        raise SystemExit(
            "No PyBoy hotkey state found. Run the emulator, press Z to save, "
            f"then try again. Expected: {paths.pyboy_hotkey_state}"
        )

    paths.fixed_state.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.pyboy_hotkey_state, paths.fixed_state)
    return paths.fixed_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Pokemon Red from a fixed relative ROM and fixed PyBoy save-state."
    )
    parser.add_argument("--steps", type=int, default=36000, help="Number of loop steps to run.")
    parser.add_argument("--window", default="SDL2", help="PyBoy window backend.")
    parser.add_argument("--no-render", action="store_true", help="Disable rendering.")
    parser.add_argument("--agent", action="store_true", help="Run the current agent instead of manual play.")
    parser.add_argument("--fix-current", action="store_true", help="Copy src/pokered.gb.state into states/fixed_start.state and exit.")
    parser.add_argument("--save-fixed-on-exit", action="store_true", help="Overwrite states/fixed_start.state with the current emulator state when the run exits.")
    parser.add_argument("--set-fixed", action="store_true", help="Alias for --save-fixed-on-exit.")
    parser.add_argument("--boot-state", action="store_true", help="Overwrite the fixed state with the ROM boot state.")
    parser.add_argument("--save-final", action="store_true", help="Save the final state to states/last.state on exit.")
    parser.add_argument("--save-every", type=int, default=0, help="Autosave every N loop steps.")
    parser.add_argument("--no-control-ui", action="store_true", help="Do not open the side save-state control panel.")
    parser.add_argument("--screenshot-every", type=int, default=0, help="Save a PNG every N loop steps.")
    parser.add_argument("--record-gif", type=Path, help="Save an animated GIF of the run.")
    parser.add_argument("--record-mp4", type=Path, help="Save an MP4 video using ffmpeg if available.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    paths = default_paths()

    if args.fix_current:
        fixed = fix_current_state(paths)
        print(f"Fixed state updated: {fixed}")
        return

    fixed_state = ensure_fixed_state(paths, force_boot_state=args.boot_state)
    print(f"ROM: {paths.rom}")
    print(f"Fixed state: {fixed_state}")
    if args.save_fixed_on_exit or args.set_fixed:
        print("When you reach the desired point, return to this terminal and press Ctrl+C.")
        print(f"The current emulator state will overwrite: {fixed_state}")

    capture_config = CaptureConfig(
        directory=paths.project_root / "captures",
        screenshot_every=args.screenshot_every,
        record_gif=args.record_gif,
        record_mp4=args.record_mp4,
    )
    save_final = None
    if args.save_fixed_on_exit or args.set_fixed:
        save_final = fixed_state
    elif args.save_final:
        save_final = paths.project_root / "states" / "last.state"

    control_panel = None
    if not args.no_control_ui:
        control_panel = _create_control_panel(paths.project_root / "states", fixed_state)

    try:
        run_rom(
            paths.rom,
            steps=args.steps,
            window=args.window,
            render=not args.no_render,
            capture_config=capture_config,
            state_dir=paths.project_root / "states",
            load_state=fixed_state,
            save_final=save_final,
            save_every=args.save_every,
            manual_play=not args.agent,
            tick_frames=1,
            control_panel=control_panel,
        )
    except KeyboardInterrupt:
        print("\nStopped by user.")
        if save_final is not None:
            print(f"Saved state: {save_final}")


def _create_control_panel(state_dir: Path, fixed_state: Path) -> QtStateControlPanel:
    try:
        return QtStateControlPanel(state_dir, fixed_state)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()

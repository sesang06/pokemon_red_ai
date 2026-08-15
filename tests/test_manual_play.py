from pathlib import Path

from pokemon_agent.cli.manual_play import ManualPlayPaths, default_paths, parse_args


def test_default_paths_are_project_relative() -> None:
    paths = default_paths()

    assert isinstance(paths, ManualPlayPaths)
    assert paths.project_root == Path.cwd()
    assert paths.rom == paths.project_root / "src" / "pokered.gb"
    assert paths.fixed_state == paths.project_root / "states" / "fixed_start.state"
    assert paths.pyboy_hotkey_state == Path(str(paths.rom) + ".state")


def test_manual_play_has_no_frame_limit_by_default() -> None:
    assert parse_args([]).steps is None
    assert parse_args(["--steps", "120"]).steps == 120

from pathlib import Path

from pokemon_agent.fixed_pokered import default_paths


def test_default_paths_are_project_relative() -> None:
    paths = default_paths()

    assert paths.project_root == Path.cwd()
    assert paths.rom == paths.project_root / "src" / "pokered.gb"
    assert paths.fixed_state == paths.project_root / "states" / "fixed_start.state"
    assert paths.pyboy_hotkey_state == Path(str(paths.rom) + ".state")

from pathlib import Path

from PIL import Image

from pokemon_agent.memory.world_state import GameState
from pokemon_agent.vision.capture import CaptureConfig, CaptureRecorder


class FakeCaptureEnvironment:
    def screen_image(self) -> Image.Image:
        return Image.new("RGB", (16, 16), "red")


def test_capture_saves_periodic_screenshot(tmp_path: Path) -> None:
    recorder = CaptureRecorder(
        FakeCaptureEnvironment(),
        CaptureConfig(directory=tmp_path, screenshot_every=2),
    )

    recorder.maybe_capture(0, GameState(map_name="Pallet Town"))
    recorder.maybe_capture(1, GameState(map_name="Pallet Town"))
    recorder.maybe_capture(2, GameState(map_name="Pallet Town"))

    assert (tmp_path / "step_000000_Pallet_Town.png").exists()
    assert not (tmp_path / "step_000001_Pallet_Town.png").exists()
    assert (tmp_path / "step_000002_Pallet_Town.png").exists()


def test_capture_saves_gif_on_close(tmp_path: Path) -> None:
    gif_path = tmp_path / "run.gif"
    recorder = CaptureRecorder(
        FakeCaptureEnvironment(),
        CaptureConfig(directory=tmp_path, record_gif=gif_path, video_fps=10),
    )

    recorder.maybe_capture(0, GameState(map_name="Pallet Town"))
    recorder.maybe_capture(1, GameState(map_name="Pallet Town"))
    recorder.close()

    assert gif_path.exists()

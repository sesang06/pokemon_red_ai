from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pokemon_agent.memory.world_state import GameState


class CaptureEnvironment(Protocol):
    def screen_image(self) -> Any:
        """Return the current PyBoy screen as a Pillow Image."""


@dataclass
class CaptureConfig:
    directory: Path = Path("captures")
    screenshot_every: int = 0
    record_gif: Path | None = None
    record_mp4: Path | None = None
    video_every: int = 1
    video_fps: int = 30
    keep_video_frames: bool = False


@dataclass
class CaptureRecorder:
    env: CaptureEnvironment
    config: CaptureConfig
    gif_frames: list[Any] = field(default_factory=list)
    mp4_frame_dir: Path | None = None
    mp4_frame_count: int = 0

    def __post_init__(self) -> None:
        self.config.directory.mkdir(parents=True, exist_ok=True)
        if self.config.record_gif is not None:
            self.config.record_gif.parent.mkdir(parents=True, exist_ok=True)
        if self.config.record_mp4 is not None:
            self.config.record_mp4.parent.mkdir(parents=True, exist_ok=True)
            stem = _safe_name(self.config.record_mp4.stem)
            self.mp4_frame_dir = self.config.directory / f"{stem}_mp4_frames_{uuid4().hex[:8]}"
            self.mp4_frame_dir.mkdir(parents=True, exist_ok=True)

    def maybe_capture(self, step: int, state: GameState) -> None:
        image = None

        if self.config.screenshot_every > 0 and step % self.config.screenshot_every == 0:
            image = self._image_copy()
            self._save_screenshot(image, step, state)

        should_record_video = (
            self.config.video_every > 0
            and step % self.config.video_every == 0
            and (self.config.record_gif is not None or self.config.record_mp4 is not None)
        )
        if should_record_video:
            image = image or self._image_copy()
            if self.config.record_gif is not None:
                self.gif_frames.append(image.copy())
            if self.config.record_mp4 is not None and self.mp4_frame_dir is not None:
                self._save_mp4_frame(image)

    def close(self) -> None:
        if self.config.record_gif is not None and self.gif_frames:
            duration_ms = max(1, round(1000 / max(self.config.video_fps, 1)))
            self.gif_frames[0].save(
                self.config.record_gif,
                save_all=True,
                append_images=self.gif_frames[1:],
                duration=duration_ms,
                loop=0,
            )
            logging.info("saved gif=%s frames=%s", self.config.record_gif, len(self.gif_frames))

        if self.config.record_mp4 is not None and self.mp4_frame_dir is not None and self.mp4_frame_count:
            self._encode_mp4()
            if not self.config.keep_video_frames:
                shutil.rmtree(self.mp4_frame_dir, ignore_errors=True)

    def _image_copy(self) -> Any:
        image = self.env.screen_image()
        if not hasattr(image, "copy"):
            raise RuntimeError("PyBoy did not return a Pillow-compatible screen image.")
        return image.copy()

    def _save_screenshot(self, image: Any, step: int, state: GameState) -> None:
        map_name = _safe_name(state.map_name)
        path = self.config.directory / f"step_{step:06d}_{map_name}.png"
        image.save(path)
        logging.info("saved screenshot=%s", path)

    def _save_mp4_frame(self, image: Any) -> None:
        if self.mp4_frame_dir is None:
            return
        path = self.mp4_frame_dir / f"frame_{self.mp4_frame_count:06d}.png"
        image.save(path)
        self.mp4_frame_count += 1

    def _encode_mp4(self) -> None:
        if self.mp4_frame_dir is None or self.config.record_mp4 is None:
            return

        if shutil.which("ffmpeg") is None:
            logging.warning("ffmpeg was not found; mp4 frames remain at %s", self.mp4_frame_dir)
            return

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-framerate",
                str(self.config.video_fps),
                "-i",
                str(self.mp4_frame_dir / "frame_%06d.png"),
                "-pix_fmt",
                "yuv420p",
                str(self.config.record_mp4),
            ],
            check=True,
        )
        logging.info("saved mp4=%s frames=%s", self.config.record_mp4, self.mp4_frame_count)


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return normalized.strip("_") or "unknown"

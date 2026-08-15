from __future__ import annotations

import ctypes
import logging
from pathlib import Path
from typing import Any


POKEMON_RED_DMG_PALETTE = (
    0xFFF8E7,
    0xF7C55D,
    0xD94B4B,
    0x203047,
)

LOGGER = logging.getLogger(__name__)


class _SdlAudioOutput:
    """Play PyBoy's public sound buffer without creating an SDL video window."""

    def __init__(self, sound: Any, volume: int):
        import sdl2

        self._sdl2 = sdl2
        self._sound = sound
        self._volume = max(0, min(int(volume), 100))
        self._device = 0
        self._source_buffer: Any | None = None
        self._mixing_buffer: Any | None = None
        self._wanted_spec: Any | None = None
        self._obtained_spec: Any | None = None

        if sdl2.SDL_InitSubSystem(sdl2.SDL_INIT_AUDIO) < 0:
            LOGGER.warning("SDL audio initialization failed: %s", sdl2.SDL_GetError().decode())
            return

        self._wanted_spec = sdl2.SDL_AudioSpec(int(sound.sample_rate), sdl2.AUDIO_S8, 2, 128)
        self._obtained_spec = sdl2.SDL_AudioSpec(0, 0, 0, 0)
        device = int(sdl2.SDL_OpenAudioDevice(None, 0, self._wanted_spec, self._obtained_spec, 0))
        if device <= 1:
            LOGGER.warning("SDL audio device failed: %s", sdl2.SDL_GetError().decode())
            return

        raw_length = int(sound.raw_buffer_length)
        source_type = ctypes.c_byte * raw_length
        self._source_buffer = source_type.from_buffer(sound.raw_buffer)
        self._mixing_buffer = source_type()
        self._device = device
        sdl2.SDL_PauseAudioDevice(self._device, 0)

    @property
    def active(self) -> bool:
        return self._device > 1

    def queue_frame(self) -> None:
        if not self.active:
            return

        length = min(int(self._sound.raw_buffer_head), int(self._sound.raw_buffer_length))
        if length <= 0:
            return

        sdl2 = self._sdl2
        max_queued_bytes = int(self._sound.raw_buffer_length) * 5
        if int(sdl2.SDL_GetQueuedAudioSize(self._device)) > max_queued_bytes:
            sdl2.SDL_ClearQueuedAudio(self._device)

        ctypes.memset(ctypes.addressof(self._mixing_buffer), 0, length)
        sdl2.SDL_MixAudioFormat(
            ctypes.cast(self._mixing_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.cast(self._source_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            sdl2.AUDIO_S8,
            length,
            self._volume * 128 // 100,
        )
        sdl2.SDL_QueueAudio(
            self._device,
            ctypes.cast(self._mixing_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            length,
        )

    def close(self) -> None:
        if not self.active:
            return
        self._sdl2.SDL_CloseAudioDevice(self._device)
        self._device = 0


class PyBoyEnvironment:
    """Thin wrapper around a live PyBoy object."""

    def __init__(
        self,
        rom_path: Path,
        window: str = "null",
        symbols: Path | None = None,
        color_palette: tuple[int, int, int, int] = POKEMON_RED_DMG_PALETTE,
        sound_emulated: bool = True,
        sound_volume: int = 100,
    ):
        try:
            from pyboy import PyBoy
        except ImportError as exc:
            raise RuntimeError("Install pyboy to run the emulator layer.") from exc

        qt_window = window.lower() == "qt"
        kwargs: dict[str, Any] = {
            "window": "null" if qt_window else window,
            "color_palette": tuple(color_palette),
            "sound_emulated": bool(sound_emulated),
            "sound_volume": max(0, min(int(sound_volume), 100)),
        }
        if symbols is not None:
            kwargs["symbols"] = str(symbols)

        self.pyboy = PyBoy(str(rom_path), **kwargs)
        self._audio_output = _SdlAudioOutput(self.pyboy.sound, sound_volume) if qt_window and sound_emulated else None

    @property
    def memory(self) -> Any:
        return self.pyboy.memory

    @property
    def game_wrapper(self) -> Any:
        return self.pyboy.game_wrapper

    def button(self, button: str, frames: int = 1) -> None:
        self.pyboy.button(button, frames)

    def tick(self, frames: int = 1, render: bool = False) -> bool:
        running = bool(self.pyboy.tick(frames, render))
        if self._audio_output is not None:
            self._audio_output.queue_frame()
        return running

    def screen_ndarray(self) -> Any:
        return self.pyboy.screen.ndarray

    def screen_image(self) -> Any:
        return self.pyboy.screen.image

    def background_tilemap(self) -> Any:
        return self.pyboy.tilemap_background[:, :]

    def window_tilemap(self) -> Any:
        return self.pyboy.tilemap_window[:, :]

    def game_area(self) -> Any:
        return self.pyboy.game_area()

    def game_area_collision(self) -> Any:
        return self.pyboy.game_area_collision()

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            self.pyboy.save_state(handle)

    def load_state(self, path: Path) -> None:
        with path.open("rb") as handle:
            self.pyboy.load_state(handle)

    def stop(self, save: bool = False) -> None:
        if self._audio_output is not None:
            self._audio_output.close()
            self._audio_output = None
        self.pyboy.stop(save=save)

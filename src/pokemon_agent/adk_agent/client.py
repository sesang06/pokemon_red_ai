from __future__ import annotations

from typing import Any, Protocol


class PokemonToolClient(Protocol):
    def start_session(self, window: str = "null", load_fixed: bool = True, control_ui: bool = False) -> dict[str, Any]:
        """Start the emulator session."""

    def stop_session(self, save_final: bool = False) -> dict[str, Any]:
        """Stop the emulator session."""

    def observe(self) -> dict[str, Any]:
        """Return the latest observation."""

    def press_button(self, button: str, frames: int = 4, after_frames: int = 8) -> dict[str, Any]:
        """Press one button."""

    def execute_actions(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute primitive button actions."""

    def step_frames(self, frames: int = 1, render: bool = False) -> dict[str, Any]:
        """Advance frames."""

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        """Save state."""

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        """Load state."""

    def reset_to_fixed(self) -> dict[str, Any]:
        """Reset to the fixed state."""

    def move_to_screen_tile(
        self,
        target_x: int,
        target_y: int,
        max_steps: int = 8,
        accept_nearest: bool = True,
    ) -> dict[str, Any]:
        """Move toward a screen tile."""

    def set_realtime_ticks(
        self,
        enabled: bool = True,
        fps: float = 60.0,
        max_frames_per_pump: int = 12,
    ) -> dict[str, Any]:
        """Configure realtime ticking outside planner actions."""

    def realtime_tick_status(self) -> dict[str, Any]:
        """Return realtime tick status."""

    def pump_realtime(self) -> dict[str, Any]:
        """Pump realtime ticks and UI events once."""


class InProcessPokemonMcpClient:
    """Call the same functions exposed as MCP tools, without a subprocess."""

    def start_session(self, window: str = "null", load_fixed: bool = True, control_ui: bool = False) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.start_session(window=window, load_fixed=load_fixed, control_ui=control_ui)

    def stop_session(self, save_final: bool = False) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.stop_session(save_final=save_final)

    def observe(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.observe()

    def press_button(self, button: str, frames: int = 4, after_frames: int = 8) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.press_button(button=button, frames=frames, after_frames=after_frames)

    def execute_actions(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.execute_actions(actions)

    def step_frames(self, frames: int = 1, render: bool = False) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.step_frames(frames=frames, render=render)

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.save_state(kind=kind, path=path)

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.load_state(kind=kind, path=path)

    def reset_to_fixed(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.reset_to_fixed()

    def move_to_screen_tile(
        self,
        target_x: int,
        target_y: int,
        max_steps: int = 8,
        accept_nearest: bool = True,
    ) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.move_to_screen_tile(
            target_x=target_x,
            target_y=target_y,
            max_steps=max_steps,
            accept_nearest=accept_nearest,
        )

    def set_realtime_ticks(
        self,
        enabled: bool = True,
        fps: float = 60.0,
        max_frames_per_pump: int = 12,
    ) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.set_realtime_ticks(
            enabled=enabled,
            fps=fps,
            max_frames_per_pump=max_frames_per_pump,
        )

    def realtime_tick_status(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.realtime_tick_status()

    def pump_realtime(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.get_session().pump_realtime()

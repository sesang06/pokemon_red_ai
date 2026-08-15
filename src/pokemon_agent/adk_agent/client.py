from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any, Protocol


class PokemonToolClient(Protocol):
    def start_session(self, window: str = "null", load_fixed: bool = True, control_ui: bool = False) -> dict[str, Any]:
        """Start the emulator session."""

    def stop_session(self, save_final: bool = False) -> dict[str, Any]:
        """Stop the emulator session."""

    def observe(self) -> dict[str, Any]:
        """Return the latest observation."""

    def press_buttons(self, buttons: list[str]) -> dict[str, Any]:
        """Press a bounded sequence of buttons."""

    def wait(self) -> dict[str, Any]:
        """Wait while the realtime ticker advances the game."""

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        """Save state."""

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        """Load state."""

    def reset_to_fixed(self) -> dict[str, Any]:
        """Reset to the fixed state."""

    def move_to_world_cell(self, target_x: int, target_y: int) -> dict[str, Any]:
        """Move toward a current-map/world coordinate."""

    def set_realtime_ticks(
        self,
        enabled: bool = True,
        fps: float = 60.0,
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

    def press_buttons(self, buttons: list[str]) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.press_buttons(buttons=buttons)

    def wait(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.wait()

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.save_state(kind=kind, path=path)

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.load_state(kind=kind, path=path)

    def reset_to_fixed(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.reset_to_fixed()

    def move_to_world_cell(self, target_x: int, target_y: int) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.move_to_world_cell(
            target_x=target_x,
            target_y=target_y,
        )

    def set_realtime_ticks(
        self,
        enabled: bool = True,
        fps: float = 60.0,
    ) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.set_realtime_ticks(
            enabled=enabled,
            fps=fps,
        )

    def realtime_tick_status(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.realtime_tick_status()

    def pump_realtime(self) -> dict[str, Any]:
        from pokemon_agent import mcp_server

        return mcp_server.get_session().pump_realtime()


class StdioPokemonMcpClient:
    """Own a persistent Pokemon MCP worker process behind synchronous client methods."""

    def __init__(
        self,
        *,
        window: str = "SDL2",
        load_fixed: bool = True,
        control_ui: bool = True,
        realtime_fps: float = 60.0,
        ui_refresh_hz: float = 30.0,
        dashboard: bool = True,
    ) -> None:
        self.window = window
        self.load_fixed = load_fixed
        self.control_ui = control_ui
        self.realtime_fps = float(realtime_fps)
        self.ui_refresh_hz = float(ui_refresh_hz)
        self.dashboard = dashboard
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._close_requested = threading.Event()
        self._startup_error: BaseException | None = None
        self._call_lock = threading.Lock()

    def start_session(
        self,
        window: str = "SDL2",
        load_fixed: bool = True,
        control_ui: bool = True,
    ) -> dict[str, Any]:
        if self._thread is not None and self._thread.is_alive():
            return {"started": True, **self.realtime_tick_status()}

        self.window = window
        self.load_fixed = load_fixed
        self.control_ui = control_ui
        self._ready.clear()
        self._close_requested.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name="pokemon-mcp-stdio-client",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=30.0):
            raise TimeoutError("Pokemon MCP worker did not initialize within 30 seconds")
        if self._startup_error is not None:
            raise RuntimeError(f"Pokemon MCP worker failed: {self._startup_error}") from self._startup_error
        status = self.realtime_tick_status()
        return {"started": True, **status}

    def stop_session(self, save_final: bool = False) -> dict[str, Any]:
        if self._thread is None or not self._thread.is_alive():
            return {"stopped": True, "already_stopped": True, "saved_path": None}
        try:
            result = self._call("stop_session", {"save_final": save_final})
        finally:
            self._close_requested.set()
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(lambda: None)
            self._thread.join(timeout=10.0)
        return result

    def observe(self) -> dict[str, Any]:
        return self._call("observe")

    def press_buttons(self, buttons: list[str]) -> dict[str, Any]:
        return self._call("press_buttons", {"buttons": list(buttons)})

    def wait(self) -> dict[str, Any]:
        return self._call("wait")

    def save_state(self, kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
        return self._call("save_state", {"kind": kind, "path": path})

    def load_state(self, kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
        return self._call("load_state", {"kind": kind, "path": path})

    def reset_to_fixed(self) -> dict[str, Any]:
        return self._call("reset_to_fixed")

    def move_to_world_cell(self, target_x: int, target_y: int) -> dict[str, Any]:
        return self._call(
            "move_to_world_cell",
            {"target_x": int(target_x), "target_y": int(target_y)},
        )

    def set_realtime_ticks(self, enabled: bool = True, fps: float = 60.0) -> dict[str, Any]:
        return self._call("set_realtime_ticks", {"enabled": enabled, "fps": float(fps)})

    def realtime_tick_status(self) -> dict[str, Any]:
        return self._call("realtime_tick_status")

    def publish_dashboard_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        return self._call("dashboard_publish_trace", {"trace": dict(trace)})

    def publish_dashboard_runtime(self, state: dict[str, Any], *, phase: str) -> dict[str, Any]:
        return self._call(
            "dashboard_publish_runtime",
            {"state": dict(state), "phase": str(phase)},
        )

    def publish_dashboard_memory(
        self,
        items: dict[str, dict[str, Any]],
        activity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "dashboard_publish_memory",
            {
                "items": {str(key): dict(value) for key, value in items.items()},
                "activity": None if activity is None else dict(activity),
            },
        )

    def pump_realtime(self) -> dict[str, Any]:
        return self.realtime_tick_status()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()

    async def _serve(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = [
            "-m",
            "pokemon_agent.mcp_server",
            "--window",
            self.window,
            "--realtime-ticks",
            "--realtime-fps",
            str(self.realtime_fps),
            "--ui-refresh-hz",
            str(self.ui_refresh_hz),
            "--transport",
            "stdio",
        ]
        if not self.load_fixed:
            command.append("--no-load-fixed")
        if not self.control_ui:
            command.append("--no-control-ui")
        if self.dashboard:
            command.append("--dashboard")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=command,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                self._loop = asyncio.get_running_loop()
                self._session = session
                self._ready.set()
                while not self._close_requested.is_set():
                    await asyncio.sleep(0.05)
        self._session = None
        self._loop = None

    def _call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        loop = self._loop
        session = self._session
        if loop is None or session is None:
            raise RuntimeError("Pokemon MCP worker is not running")
        with self._call_lock:
            future = asyncio.run_coroutine_threadsafe(
                session.call_tool(name, arguments or {}),
                loop,
            )
            result = future.result(timeout=120.0)
        if result.is_error:
            message = "\n".join(str(getattr(item, "text", item)) for item in result.content)
            raise RuntimeError(f"MCP {name} failed: {message}")
        if isinstance(result.structured_content, dict):
            return dict(result.structured_content)
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
        return {}

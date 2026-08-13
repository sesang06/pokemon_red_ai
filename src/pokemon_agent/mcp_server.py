from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from typing import Any

from pokemon_agent.mcp_logging import McpCommandLog
from pokemon_agent.session import PokemonSession

try:
    from mcp.server.mcpserver.server import MCPServer
except ModuleNotFoundError:  # pragma: no cover - exercised only without the optional dependency.
    MCPServer = None  # type: ignore[assignment]


class _MissingFastMCP:
    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            return func

        return decorator

    def resource(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            return func

        return decorator

    def run(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError('Install MCP support first: python -m pip install -e ".[dev,vision,memory]"')


mcp = MCPServer("pokemon-red-pyboy") if MCPServer is not None else _MissingFastMCP()
_CALL_LOG = McpCommandLog()
_SESSION = PokemonSession()
_SESSION.set_mcp_log_provider(lambda: _CALL_LOG.format_recent())


def set_session_for_tests(session: PokemonSession) -> None:
    global _SESSION
    _CALL_LOG.clear()
    _SESSION = session
    _SESSION.set_mcp_log_provider(lambda: _CALL_LOG.format_recent())


def get_session() -> PokemonSession:
    return _SESSION


def _run_logged_tool(name: str, args: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    entry_id = _CALL_LOG.started(name, args)
    try:
        result = operation()
    except Exception as exc:
        _CALL_LOG.failed(entry_id, exc)
        raise
    _CALL_LOG.completed(entry_id, result)
    return result


@mcp.tool()
def start_session(window: str = "null", load_fixed: bool = True, control_ui: bool = True) -> dict[str, Any]:
    """Start the fixed Pokemon Red PyBoy session and optionally show the PySide6 control panel."""

    return _run_logged_tool(
        "start_session",
        {"window": window, "load_fixed": load_fixed, "control_ui": control_ui},
        lambda: get_session().start(window=window, load_fixed=load_fixed, control_ui=control_ui),
    )


@mcp.tool()
def stop_session(save_final: bool = False) -> dict[str, Any]:
    """Stop the active PyBoy session."""

    return _run_logged_tool(
        "stop_session",
        {"save_final": save_final},
        lambda: get_session().stop(save_final=save_final),
    )


@mcp.tool()
def observe() -> dict[str, Any]:
    """Observe RAM, screen tiles, collision, and a PNG base64 screenshot."""

    return _run_logged_tool("observe", {}, lambda: get_session().observe())


@mcp.tool()
def press_button(button: str, frames: int = 4, after_frames: int = 8) -> dict[str, Any]:
    """Press one Game Boy button and return the new observation."""

    return _run_logged_tool(
        "press_button",
        {"button": button, "frames": frames, "after_frames": after_frames},
        lambda: get_session().press_button(button=button, frames=frames, after_frames=after_frames),
    )


@mcp.tool()
def execute_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute a bounded list of button actions."""

    return _run_logged_tool(
        "execute_actions",
        {"actions": actions},
        lambda: get_session().execute_actions(actions),
    )


@mcp.tool()
def step_frames(frames: int = 1, render: bool = False) -> dict[str, Any]:
    """Advance the emulator without pressing buttons."""

    return _run_logged_tool(
        "step_frames",
        {"frames": frames, "render": render},
        lambda: get_session().step_frames(frames=frames, render=render),
    )


@mcp.tool()
def save_state(kind: str = "snapshot", path: str | None = None) -> dict[str, Any]:
    """Save a PyBoy state inside the project states directory."""

    return _run_logged_tool(
        "save_state",
        {"kind": kind, "path": path},
        lambda: get_session().save_state(kind=kind, path=path),
    )


@mcp.tool()
def load_state(kind: str = "fixed", path: str | None = None) -> dict[str, Any]:
    """Load a PyBoy state from the project states directory."""

    return _run_logged_tool(
        "load_state",
        {"kind": kind, "path": path},
        lambda: get_session().load_state(kind=kind, path=path),
    )


@mcp.tool()
def reset_to_fixed() -> dict[str, Any]:
    """Reload states/fixed_start.state."""

    return _run_logged_tool("reset_to_fixed", {}, lambda: get_session().reset_to_fixed())


@mcp.tool()
def move_to_screen_tile(
    target_x: int,
    target_y: int,
    max_steps: int = 8,
    accept_nearest: bool = True,
) -> dict[str, Any]:
    """Move toward a current-screen game_area tile using collision-aware Dijkstra routing."""

    return _run_logged_tool(
        "move_to_screen_tile",
        {
            "target_x": target_x,
            "target_y": target_y,
            "max_steps": max_steps,
            "accept_nearest": accept_nearest,
        },
        lambda: get_session().move_to_screen_tile(
            target_x=target_x,
            target_y=target_y,
            max_steps=max_steps,
            accept_nearest=accept_nearest,
        ),
    )


@mcp.tool()
def recent_mcp_commands(limit: int = 50) -> dict[str, Any]:
    """Return recent MCP tool calls and the same text shown in the control panel."""

    entry_id = _CALL_LOG.started("recent_mcp_commands", {"limit": limit})
    try:
        _CALL_LOG.completed(entry_id, {"commands": _CALL_LOG.recent(limit)})
        return {
            "commands": _CALL_LOG.recent(limit),
            "text": _CALL_LOG.format_recent(limit),
        }
    except Exception as exc:
        _CALL_LOG.failed(entry_id, exc)
        raise


@mcp.tool()
def set_realtime_ticks(
    enabled: bool = True,
    fps: float = 60.0,
    max_frames_per_pump: int = 12,
) -> dict[str, Any]:
    """Enable or disable non-planner realtime emulator ticks."""

    return _run_logged_tool(
        "set_realtime_ticks",
        {"enabled": enabled, "fps": fps, "max_frames_per_pump": max_frames_per_pump},
        lambda: get_session().set_realtime_ticking(
            enabled=enabled,
            fps=fps,
            max_frames_per_pump=max_frames_per_pump,
        ),
    )


@mcp.tool()
def realtime_tick_status() -> dict[str, Any]:
    """Return the current non-planner realtime tick configuration."""

    return _run_logged_tool(
        "realtime_tick_status",
        {},
        lambda: get_session().realtime_tick_status(),
    )


@mcp.resource("pokemon://state/latest")
def latest_state() -> str:
    """Return the latest structured state observation as JSON."""

    return json.dumps(observe()["state"], ensure_ascii=False)


@mcp.resource("pokemon://ram/latest")
def latest_ram() -> str:
    """Return the latest RAM watch text."""

    return observe()["ram_watch"]


@mcp.resource("pokemon://game-area/latest")
def latest_game_area() -> str:
    """Return the latest game_area matrix as JSON."""

    return json.dumps(observe()["game_area"])


@mcp.resource("pokemon://collision/latest")
def latest_collision() -> str:
    """Return the latest game_area_collision matrix as JSON."""

    return json.dumps(observe()["game_area_collision"])


@mcp.resource("pokemon://mcp-log/recent")
def latest_mcp_log() -> str:
    """Return the latest MCP command log text."""

    return _CALL_LOG.format_recent()


@mcp.resource("pokemon://realtime/status")
def latest_realtime_status() -> str:
    """Return realtime tick configuration as JSON."""

    return json.dumps(get_session().realtime_tick_status(), ensure_ascii=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Pokemon Red PyBoy MCP server.")
    parser.add_argument("--window", default="null", help="PyBoy window backend, for example null or SDL2.")
    parser.add_argument("--no-load-fixed", action="store_true", help="Do not load states/fixed_start.state on startup.")
    parser.add_argument("--no-control-ui", action="store_true", help="Do not open the PySide6 control panel on startup.")
    parser.add_argument("--no-auto-start", action="store_true", help="Start only the MCP transport and wait for start_session.")
    parser.add_argument("--realtime-ticks", action="store_true", help="Tick the emulator continuously outside planner/tool actions.")
    parser.add_argument("--realtime-fps", type=float, default=60.0, help="Game frames per second for --realtime-ticks.")
    parser.add_argument("--ui-refresh-hz", type=float, default=30.0, help="How often to pump realtime ticks and the control panel.")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport to run.",
    )
    parser.add_argument("--log-level", default="WARNING", help="Python logging level.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    if not args.no_auto_start:
        get_session().start(
            window=args.window,
            load_fixed=not args.no_load_fixed,
            control_ui=not args.no_control_ui,
        )
    if args.realtime_ticks:
        get_session().set_realtime_ticking(enabled=True, fps=args.realtime_fps)
    if args.transport == "stdio":
        _run_stdio_with_control_panel_pump(ui_refresh_hz=args.ui_refresh_hz)
    else:
        mcp.run(args.transport)


def _run_stdio_with_control_panel_pump(*, ui_refresh_hz: float = 30.0) -> None:
    import anyio

    refresh_delay = 1.0 / max(1.0, min(float(ui_refresh_hz), 120.0))

    async def run() -> None:
        async with anyio.create_task_group() as task_group:
            async def run_server() -> None:
                try:
                    await mcp.run_stdio_async()
                finally:
                    task_group.cancel_scope.cancel()

            async def pump_ui() -> None:
                while True:
                    get_session().pump_realtime()
                    await anyio.sleep(refresh_delay)

            task_group.start_soon(run_server)
            task_group.start_soon(pump_ui)

    anyio.run(run)


if __name__ == "__main__":
    main()

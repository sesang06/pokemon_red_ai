from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from pokemon_agent.dashboard.events import LiveEventHub


LOGGER = logging.getLogger(__name__)
DEFAULT_FRONTEND_DIST = Path(__file__).with_name("static")


def create_dashboard_app(
    hub: LiveEventHub,
    *,
    frontend_dist: Path | str = DEFAULT_FRONTEND_DIST,
) -> FastAPI:
    app = FastAPI(title="Pokemon Red Runtime Debugger", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        snapshot = hub.snapshot()
        emulator = snapshot["state"].get("emulator", {})
        return {
            "status": "ok",
            "revision": snapshot["revision"],
            "emulator_status": emulator.get("status"),
            "frame_index": emulator.get("frame_index"),
        }

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, Any]:
        return hub.snapshot()

    @app.websocket("/ws/live")
    async def live_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        subscription = hub.subscribe()
        try:
            while True:
                await websocket.send_json(await subscription.queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(subscription)

    dist = Path(frontend_dist)
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:
        @app.get("/")
        async def frontend_missing() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "frontend_not_built",
                    "message": "Run the frontend build in dashboard/frontend.",
                    "expected": str(dist / "index.html"),
                },
                status_code=503,
            )

    return app


class DashboardRuntimeStateStore:
    def __init__(self, file_store: Any, hub: LiveEventHub, *, memory_store: Any | None = None) -> None:
        self.file_store = file_store
        self.hub = hub
        self.memory_store = memory_store

    def publish(self, state: dict[str, Any], *, phase: str) -> None:
        self.file_store.publish(state, phase=phase)
        self.hub.publish_runtime(state, phase=phase)
        if self.memory_store is not None and phase in {"interpreted", "completed"}:
            self.hub.publish_memory_snapshot(self.memory_store.items())


class DashboardService:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        frontend_dist: Path | str = DEFAULT_FRONTEND_DIST,
        hub: LiveEventHub | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.frontend_dist = Path(frontend_dist)
        self.hub = hub or LiveEventHub()
        self._server: Any | None = None
        self._thread: threading.Thread | None = None
        self._session: Any | None = None
        self._observation_listener: Callable[[dict[str, Any]], None] | None = None
        self._pending_observation: dict[str, Any] | None = None
        self._observation_lock = threading.Lock()
        self._observation_event = threading.Event()
        self._observation_stop = threading.Event()
        self._observation_thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        display_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{display_host}:{self.port}"

    def attach_session(self, session: Any) -> None:
        if self._session is session:
            return
        self.detach_session()

        def listener(observation: dict[str, Any]) -> None:
            with self._observation_lock:
                self._pending_observation = observation
            self._observation_event.set()

        self._session = session
        self._observation_listener = listener
        self._observation_stop.clear()
        self._observation_thread = threading.Thread(
            target=self._publish_observations,
            name="pokemon-dashboard-observations",
            daemon=True,
        )
        self._observation_thread.start()
        session.add_observation_listener(listener)
        try:
            listener(session.peek_observation())
        except Exception:
            LOGGER.debug("Dashboard initial observation is not ready", exc_info=True)

    def detach_session(self) -> None:
        if self._session is not None and self._observation_listener is not None:
            self._session.remove_observation_listener(self._observation_listener)
        self._observation_stop.set()
        self._observation_event.set()
        if self._observation_thread is not None and self._observation_thread is not threading.current_thread():
            self._observation_thread.join(timeout=1.0)
        self._session = None
        self._observation_listener = None
        self._observation_thread = None
        with self._observation_lock:
            self._pending_observation = None

    def _publish_observations(self) -> None:
        while not self._observation_stop.is_set():
            self._observation_event.wait(timeout=0.25)
            self._observation_event.clear()
            if self._observation_stop.is_set():
                return
            with self._observation_lock:
                observation = self._pending_observation
                self._pending_observation = None
            if observation is None or self._session is None:
                continue
            try:
                self.hub.publish_observation(observation, ticker=self._session.realtime_tick_status())
            except Exception:
                LOGGER.exception("Dashboard observation publishing failed")

    def start(self, *, timeout: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            import uvicorn
        except ModuleNotFoundError as exc:
            raise RuntimeError('Install dashboard dependencies with: python -m pip install -e ".[dev]"') from exc

        app = create_dashboard_app(self.hub, frontend_dist=self.frontend_dist)
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="pokemon-dashboard-server",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if bool(getattr(self._server, "started", False)):
                self.hub.publish_system_event("DASHBOARD_CONNECTED", f"Dashboard listening at {self.url}")
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.02)
        self.stop()
        raise RuntimeError(f"Dashboard server did not start at {self.url}")

    def stop(self) -> None:
        self.detach_session()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._server = None


def compose_trace_sinks(*sinks: Callable[[dict[str, Any]], None] | None) -> Callable[[dict[str, Any]], None]:
    active = [sink for sink in sinks if sink is not None]

    def publish(event: dict[str, Any]) -> None:
        for sink in active:
            try:
                sink(event)
            except Exception:
                LOGGER.exception("Agent trace sink failed")

    return publish

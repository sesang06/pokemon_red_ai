from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import Literal

from pokemon_agent.memory.world_state import GameState

ControlAction = Literal["save_state", "load_state", "stop"]


@dataclass(frozen=True)
class ControlCommand:
    action: ControlAction
    path: Path | None = None


class QtStateControlPanel:
    def __init__(self, state_dir: Path, fixed_state: Path):
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import (
                QApplication,
                QCheckBox,
                QGridLayout,
                QLabel,
                QPlainTextEdit,
                QPushButton,
                QTabWidget,
                QVBoxLayout,
                QWidget,
            )
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed. Run: uv sync") from exc

        self.state_dir = state_dir
        self.fixed_state = fixed_state
        self.commands: list[ControlCommand] = []
        self.last_state: GameState | None = None
        self.closing = False
        self.app = QApplication.instance() or QApplication([])

        self.window = QWidget()
        self.window.setWindowTitle("Pokemon Red State Control")
        self.window.setFixedSize(760, 980)
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        layout = QVBoxLayout()
        self.window.setLayout(layout)

        self.state_label = QLabel("State: waiting for emulator...")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        self.status_label = QLabel(f"Fixed: {fixed_state}")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.screen_label = QLabel("Waiting for screen...")
        self.screen_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_label.setFixedSize(320, 288)
        self.screen_label.setStyleSheet("background: #111; color: #ddd; border: 1px solid #444;")
        layout.addWidget(self.screen_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        button_grid = QGridLayout()
        layout.addLayout(button_grid)

        save_fixed = QPushButton("Save Fixed")
        save_fixed.clicked.connect(self._queue_save_fixed)
        button_grid.addWidget(save_fixed, 0, 0)

        save_snapshot = QPushButton("Save Snapshot")
        save_snapshot.clicked.connect(self._queue_save_snapshot)
        button_grid.addWidget(save_snapshot, 0, 1)

        load_fixed = QPushButton("Load Fixed")
        load_fixed.clicked.connect(self._queue_load_fixed)
        button_grid.addWidget(load_fixed, 1, 0)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self._queue_stop)
        button_grid.addWidget(quit_button, 1, 1)

        self.save_fixed_on_quit = QCheckBox("Save fixed state when quitting")
        layout.addWidget(self.save_fixed_on_quit)

        self.ram_label = QLabel("RAM map, game_area, game_area_collision, world map, and MCP log update once per second.")
        layout.addWidget(self.ram_label)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        self.ram_text = QPlainTextEdit()
        self._prepare_text_box(self.ram_text, "Waiting for RAM sample...")
        tabs.addTab(self.ram_text, "RAM Map")

        self.game_area_text = QPlainTextEdit()
        self._prepare_text_box(self.game_area_text, "Waiting for game_area sample...")
        tabs.addTab(self.game_area_text, "Game Area")

        self.collision_text = QPlainTextEdit()
        self._prepare_text_box(self.collision_text, "Waiting for game_area_collision sample...")
        tabs.addTab(self.collision_text, "Collision")

        self.world_map_text = QPlainTextEdit()
        self._prepare_text_box(self.world_map_text, "Waiting for dynamic world map...")
        tabs.addTab(self.world_map_text, "World Map")

        self.mcp_log_text = QPlainTextEdit()
        self._prepare_text_box(self.mcp_log_text, "Waiting for MCP tool calls...")
        tabs.addTab(self.mcp_log_text, "MCP Log")

        self.window.closeEvent = self._handle_close_event
        self.window.show()

    def poll(self, state: GameState) -> list[ControlCommand]:
        self.last_state = state
        self.state_label.setText(f"State: {state.summary()}")
        self.app.processEvents()

        commands = list(self.commands)
        self.commands.clear()
        return commands

    def notify_saved(self, path: Path) -> None:
        self.status_label.setText(f"Saved: {path}")

    def notify_loaded(self, path: Path) -> None:
        self.status_label.setText(f"Loaded: {path}")

    def notify_error(self, message: str) -> None:
        self.status_label.setText(f"Error: {message}")

    def update_ram_text(self, text: str) -> None:
        self.ram_text.setPlainText(text)

    def update_game_area_text(self, text: str) -> None:
        self.game_area_text.setPlainText(text)

    def update_collision_text(self, text: str) -> None:
        self.collision_text.setPlainText(text)

    def update_world_map_text(self, text: str) -> None:
        self.world_map_text.setPlainText(text)

    def update_mcp_log_text(self, text: str) -> None:
        self.mcp_log_text.setPlainText(text)

    def update_screen_image(self, image: object) -> None:
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QPixmap
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed. Run: uv sync") from exc

        if not hasattr(image, "save"):
            self.screen_label.setText("Screen image unavailable")
            return

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        if not pixmap.loadFromData(buffer.getvalue(), "PNG"):
            self.screen_label.setText("Screen image unavailable")
            return

        self.screen_label.setPixmap(
            pixmap.scaled(
                self.screen_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def close(self) -> None:
        self.closing = True
        self.window.close()
        self.app.processEvents()

    def _queue_save_fixed(self) -> None:
        self.commands.append(ControlCommand("save_state", self.fixed_state))

    def _queue_save_snapshot(self) -> None:
        self.commands.append(ControlCommand("save_state", self._snapshot_path()))

    def _queue_load_fixed(self) -> None:
        self.commands.append(ControlCommand("load_state", self.fixed_state))

    def _queue_stop(self) -> None:
        if self.save_fixed_on_quit.isChecked():
            self.commands.append(ControlCommand("save_state", self.fixed_state))
        self.commands.append(ControlCommand("stop"))

    def _handle_close_event(self, event: object) -> None:
        if self.closing:
            if hasattr(event, "accept"):
                event.accept()
            return
        self._queue_stop()
        if hasattr(event, "ignore"):
            event.ignore()

    def _snapshot_path(self) -> Path:
        state = self.last_state or GameState()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        map_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", state.map_name.strip()).strip("_") or "unknown"
        return self.state_dir / f"snapshot_{timestamp}_{map_name}.state"

    def _prepare_text_box(self, text_box: object, text: str) -> None:
        text_box.setReadOnly(True)
        text_box.setLineWrapMode(text_box.LineWrapMode.NoWrap)
        text_box.setPlainText(text)
        font = text_box.font()
        font.setFamily("Consolas")
        font.setPointSize(9)
        text_box.setFont(font)

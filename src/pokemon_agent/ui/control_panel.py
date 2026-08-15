from __future__ import annotations

import re
import json
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import Literal

from pokemon_agent.memory.world_state import GameState

ControlAction = Literal["save_state", "load_state", "stop", "move", "buttons"]
VALID_BUTTON_TOKENS = {"a", "b", "start", "select", "left", "right", "up", "down", "wait"}


@dataclass(frozen=True)
class ControlCommand:
    action: ControlAction
    path: Path | None = None
    target: tuple[int, int] | None = None
    buttons: tuple[str, ...] = ()


class QtStateControlPanel:
    def __init__(self, state_dir: Path, fixed_state: Path):
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import (
                QApplication,
                QCheckBox,
                QGridLayout,
                QHBoxLayout,
                QLabel,
                QLineEdit,
                QPlainTextEdit,
                QPushButton,
                QSpinBox,
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
        self._move_inputs_initialized = False
        self.closing = False
        self.app = QApplication.instance() or QApplication([])

        self.window = QWidget()
        self.window.setWindowTitle("Pokemon Red")
        self.window.resize(1160, 900)
        self.window.setMinimumSize(900, 700)
        self.window.move(40, 40)
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        layout = QVBoxLayout()
        self.window.setLayout(layout)

        self.state_label = QLabel("State: waiting for emulator...")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        self.status_label = QLabel(f"Fixed: {fixed_state}")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        visual_layout = QHBoxLayout()
        layout.addLayout(visual_layout)

        screen_layout = QVBoxLayout()
        screen_title = QLabel("Game Screen")
        screen_layout.addWidget(screen_title)
        self.screen_label = QLabel("Waiting for screen...")
        self.screen_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_label.setMinimumSize(320, 288)
        self.screen_label.setMaximumSize(560, 504)
        self.screen_label.setStyleSheet("background: #111; color: #ddd; border: 1px solid #444;")
        screen_layout.addWidget(self.screen_label)
        visual_layout.addLayout(screen_layout, 1)

        overlay_layout = QVBoxLayout()
        overlay_title = QLabel("Collision Overlay + World Coordinates")
        overlay_layout.addWidget(overlay_title)
        self.overlay_label = QLabel("Waiting for collision overlay...")
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setMinimumSize(320, 288)
        self.overlay_label.setMaximumSize(560, 504)
        self.overlay_label.setStyleSheet("background: #111; color: #ddd; border: 1px solid #444;")
        overlay_layout.addWidget(self.overlay_label)
        visual_layout.addLayout(overlay_layout, 1)

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

        self.move_hint_label = QLabel("Move target uses current map coordinates from the overlay/status.")
        self.move_hint_label.setWordWrap(True)
        layout.addWidget(self.move_hint_label)

        self.move_x_input = QSpinBox()
        self.move_x_input.setRange(0, 255)
        self.move_x_input.setValue(0)
        self.move_x_input.setPrefix("X ")
        self.move_x_input.setToolTip("Current map x coordinate shown in the overlay/status")
        button_grid.addWidget(self.move_x_input, 2, 0)

        self.move_y_input = QSpinBox()
        self.move_y_input.setRange(0, 255)
        self.move_y_input.setValue(0)
        self.move_y_input.setPrefix("Y ")
        self.move_y_input.setToolTip("Current map y coordinate shown in the overlay/status")
        button_grid.addWidget(self.move_y_input, 2, 1)

        move_button = QPushButton("Move")
        move_button.clicked.connect(self._queue_move)
        move_button.setToolTip("Queue a move toward the requested current-map coordinate")
        button_grid.addWidget(move_button, 3, 0, 1, 2)

        self.buttons_hint_label = QLabel('Buttons array accepts: a, b, start, select, left, right, up, down, wait.')
        self.buttons_hint_label.setWordWrap(True)
        layout.addWidget(self.buttons_hint_label)

        self.buttons_input = QLineEdit()
        self.buttons_input.setPlaceholderText('["a","wait"] or a, wait')
        self.buttons_input.setToolTip('Queue {"type":"buttons","buttons":[...]} with a delay between tokens')
        button_grid.addWidget(self.buttons_input, 4, 0)

        buttons_button = QPushButton("Buttons")
        buttons_button.clicked.connect(self._queue_buttons)
        buttons_button.setToolTip("Queue a delayed button-token array")
        button_grid.addWidget(buttons_button, 4, 1)

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
        self._sync_move_inputs_once(state)
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

    def notify_move_result(self, result: dict[str, object]) -> None:
        target = result.get("requested_world_cell")
        resolved = result.get("resolved_world_cell")
        actions = result.get("executed_actions")
        buttons: list[str] = []
        if isinstance(actions, list):
            buttons = [str(action.get("button")) for action in actions if isinstance(action, dict)]
        self.status_label.setText(
            "Move: "
            f"target={target} "
            f"resolved={resolved} "
            f"stop={result.get('stop_reason')} "
            f"steps={result.get('steps_taken')} "
            f"actions={buttons}"
        )

    def notify_buttons_result(self, result: dict[str, object]) -> None:
        actions = result.get("executed_actions")
        buttons: list[str] = []
        if isinstance(actions, list):
            buttons = [str(action.get("button")) for action in actions if isinstance(action, dict)]
        self.status_label.setText(
            "Buttons: "
            f"requested={result.get('requested_buttons')} "
            f"stop={result.get('stop_reason')} "
            f"steps={result.get('steps_taken')} "
            f"actions={buttons}"
        )

    def update_ram_text(self, text: str) -> None:
        self._set_plain_text_preserving_view(self.ram_text, text)

    def update_game_area_text(self, text: str) -> None:
        self._set_plain_text_preserving_view(self.game_area_text, text)

    def update_collision_text(self, text: str) -> None:
        self._set_plain_text_preserving_view(self.collision_text, text)

    def update_world_map_text(self, text: str) -> None:
        self._set_plain_text_preserving_view(self.world_map_text, text)

    def update_mcp_log_text(self, text: str) -> None:
        self._set_plain_text_preserving_view(self.mcp_log_text, text)

    def update_screen_image(self, image: object) -> None:
        self._set_image_label(self.screen_label, image, "Screen image unavailable")

    def update_overlay_image(self, image: object) -> None:
        self._set_image_label(self.overlay_label, image, "Collision overlay unavailable")

    def close(self) -> None:
        self.closing = True
        self.window.close()
        self.app.processEvents()

    def _set_image_label(self, label: object, image: object, unavailable_text: str) -> None:
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QPixmap
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed. Run: uv sync") from exc

        if not hasattr(image, "save"):
            label.setText(unavailable_text)
            return

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        if not pixmap.loadFromData(buffer.getvalue(), "PNG"):
            label.setText(unavailable_text)
            return

        label.setPixmap(
            pixmap.scaled(
                label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def _queue_save_fixed(self) -> None:
        self.commands.append(ControlCommand("save_state", self.fixed_state))

    def _queue_save_snapshot(self) -> None:
        self.commands.append(ControlCommand("save_state", self._snapshot_path()))

    def _queue_load_fixed(self) -> None:
        self.commands.append(ControlCommand("load_state", self.fixed_state))

    def _queue_move(self) -> None:
        target = (int(self.move_x_input.value()), int(self.move_y_input.value()))
        self.commands.append(ControlCommand("move", target=target))
        self.status_label.setText(f"Queued move: world_target={list(target)}")

    def _queue_buttons(self) -> None:
        try:
            buttons = _parse_buttons_array(self.buttons_input.text())
        except ValueError as exc:
            self.status_label.setText(f"Error: {exc}")
            return

        self.commands.append(ControlCommand("buttons", buttons=tuple(buttons)))
        self.status_label.setText(f"Queued buttons: {buttons}")

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

    def _sync_move_inputs_once(self, state: GameState) -> None:
        if getattr(self, "_move_inputs_initialized", False):
            return
        if state.position is None:
            return
        self.move_x_input.setValue(state.position.x)
        self.move_y_input.setValue(state.position.y)
        self._move_inputs_initialized = True

    def _prepare_text_box(self, text_box: object, text: str) -> None:
        text_box.setReadOnly(True)
        text_box.setLineWrapMode(text_box.LineWrapMode.NoWrap)
        text_box.setPlainText(text)
        font = text_box.font()
        font.setFamily("Consolas")
        font.setPointSize(9)
        text_box.setFont(font)

    def _set_plain_text_preserving_view(self, text_box: object, text: str) -> None:
        if text_box.toPlainText() == text:
            return

        vertical_scroll = text_box.verticalScrollBar()
        horizontal_scroll = text_box.horizontalScrollBar()
        vertical_value = vertical_scroll.value()
        horizontal_value = horizontal_scroll.value()
        was_at_bottom = vertical_value == vertical_scroll.maximum()
        cursor_position = text_box.textCursor().position()

        text_box.setPlainText(text)

        cursor = text_box.textCursor()
        cursor.setPosition(min(cursor_position, len(text)))
        text_box.setTextCursor(cursor)

        if was_at_bottom:
            vertical_scroll.setValue(vertical_scroll.maximum())
        else:
            vertical_scroll.setValue(min(vertical_value, vertical_scroll.maximum()))
        horizontal_scroll.setValue(min(horizontal_value, horizontal_scroll.maximum()))


def _parse_buttons_array(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("buttons array is empty")

    raw_values: object
    if cleaned.startswith("["):
        try:
            raw_values = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("buttons array must be valid JSON or comma-separated text") from exc
    else:
        raw_values = re.split(r"[\s,]+", cleaned)

    if not isinstance(raw_values, list):
        raise ValueError("buttons array must be a list")

    buttons: list[str] = []
    for raw_button in raw_values:
        if not isinstance(raw_button, str):
            raise ValueError("buttons array values must be strings")
        button = raw_button.strip().lower()
        if not button:
            continue
        if button not in VALID_BUTTON_TOKENS:
            raise ValueError(f"invalid button token: {button}")
        buttons.append(button)

    if not buttons:
        raise ValueError("buttons array is empty")
    if len(buttons) > 16:
        raise ValueError("buttons array accepts at most 16 tokens")
    return buttons

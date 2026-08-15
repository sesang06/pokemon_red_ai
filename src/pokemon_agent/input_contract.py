from __future__ import annotations


GAME_BUTTON_NAMES = ("a", "b", "start", "select", "left", "right", "up", "down")
WAIT_TOKEN = "wait"
BUTTON_TOKENS = GAME_BUTTON_NAMES + (WAIT_TOKEN,)
MAX_BUTTONS_PER_ACTION = 16
MAX_MOVE_PATH_STEPS = 8
MAX_WORLD_NAVIGATION_SEGMENTS = 64
MAX_MOVE_WAYPOINTS = 8

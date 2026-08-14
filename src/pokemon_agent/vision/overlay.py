from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pokemon_agent.tools.pathfinding import GridPoint
from pokemon_agent.tools.screen_navigation import (
    PLAYER_SCREEN_TILE,
    WALK_CELL_SIZE,
    compress_collision_to_walk_grid,
    walk_cell_to_map_position,
)

DEFAULT_OVERLAY_SCALE = 4
WALKABLE_FILL = (20, 170, 80, 70)
WALKABLE_OUTLINE = (80, 255, 140, 210)
BLOCKED_FILL = (230, 45, 45, 95)
BLOCKED_OUTLINE = (255, 90, 90, 230)
GRID_OUTLINE = (255, 255, 255, 70)
TEXT_BG = (0, 0, 0, 165)
TEXT_FILL = (255, 255, 255, 255)
PLAYER_OUTLINE = (255, 235, 40, 255)


def render_collision_overlay(
    screen_image: Image.Image,
    collision: Any,
    *,
    player_position: Any | None = None,
    scale: int = DEFAULT_OVERLAY_SCALE,
) -> Image.Image:
    source_rows = _to_rows(collision)
    if not source_rows:
        return screen_image.copy()

    source_height = len(source_rows)
    source_width = max(len(row) for row in source_rows)
    if source_width <= 0 or source_height <= 0:
        return screen_image.copy()

    walk_rows = compress_collision_to_walk_grid(source_rows)
    walk_height = len(walk_rows)
    walk_width = max((len(row) for row in walk_rows), default=0)
    if walk_width <= 0 or walk_height <= 0:
        return screen_image.copy()

    scale = max(1, int(scale))
    source_tile_width = max(1, screen_image.width // source_width)
    source_tile_height = max(1, screen_image.height // source_height)
    tile_width = source_tile_width * scale
    tile_height = source_tile_height * scale
    block_width = tile_width * WALK_CELL_SIZE
    block_height = tile_height * WALK_CELL_SIZE
    output_size = (source_width * tile_width, source_height * tile_height)

    image = screen_image.convert("RGBA").resize(output_size, Image.Resampling.NEAREST)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    map_font = _load_font(max(10, min(tile_width, tile_height) // 2))

    for walk_y in range(walk_height):
        row = walk_rows[walk_y] if walk_y < len(walk_rows) else []
        for walk_x in range(walk_width):
            value = row[walk_x] if walk_x < len(row) else 0
            x0 = walk_x * block_width
            y0 = walk_y * block_height
            x1 = x0 + block_width - 1
            y1 = y0 + block_height - 1
            fill, outline = (WALKABLE_FILL, WALKABLE_OUTLINE) if bool(value) else (BLOCKED_FILL, BLOCKED_OUTLINE)
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=outline)
            draw.rectangle((x0, y0, x1, y1), outline=GRID_OUTLINE)

    grid_player_position = _position_to_grid_point(player_position)
    if grid_player_position is not None:
        _draw_map_position_labels(
            draw,
            map_font,
            grid_player_position,
            walk_width,
            walk_height,
            tile_width,
            tile_height,
        )

    _draw_player_marker(draw, tile_width, tile_height, source_width, source_height)
    return Image.alpha_composite(image, overlay).convert("RGB")


def overlay_metadata(
    image: Image.Image,
    *,
    player_position: Any | None = None,
    scale: int = DEFAULT_OVERLAY_SCALE,
) -> dict[str, Any]:
    grid_player_position = _position_to_grid_point(player_position)
    metadata = {
        "scale": int(scale),
        "source_width": 160,
        "source_height": 144,
        "tile_columns": 20,
        "tile_rows": 18,
        "walk_cell_columns": 10,
        "walk_cell_rows": 9,
        "walk_cell_size": WALK_CELL_SIZE,
        "collision_truthy": "walkable",
        "coordinate_formula": "map_x = player_x + floor(screen_tile_x / 2) - 4; map_y = player_y + floor(screen_tile_y / 2) - 4",
        "overlays": ["walk_cell_map_coordinates", "walk_area_collision", "player_walk_cell"],
        "legend": {
            "green": "walkable 2x2 walk area",
            "red": "blocked 2x2 walk area",
            "yellow": "player walk cell",
            "(x, y)": "map coordinate for the 2x2 walk cell",
        },
        "width": image.width,
        "height": image.height,
    }
    if grid_player_position is not None:
        metadata["player_map_position"] = {"x": grid_player_position.x, "y": grid_player_position.y}
    return metadata


def _draw_player_marker(
    draw: ImageDraw.ImageDraw,
    tile_width: int,
    tile_height: int,
    width: int,
    height: int,
) -> None:
    x = PLAYER_SCREEN_TILE.x
    y = PLAYER_SCREEN_TILE.y
    if not (0 <= x < width and 0 <= y < height):
        return

    x0 = x * tile_width
    y0 = y * tile_height
    x1 = x0 + tile_width * WALK_CELL_SIZE - 1
    y1 = y0 + tile_height * WALK_CELL_SIZE - 1
    draw.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), outline=PLAYER_OUTLINE, width=max(2, tile_width // 8))


def _draw_map_position_labels(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    player_position: GridPoint,
    walk_width: int,
    walk_height: int,
    tile_width: int,
    tile_height: int,
) -> None:
    block_width = tile_width * WALK_CELL_SIZE
    block_height = tile_height * WALK_CELL_SIZE
    for walk_y in range(walk_height):
        for walk_x in range(walk_width):
            map_position = walk_cell_to_map_position(GridPoint(walk_x, walk_y), player_position)
            label = f"({map_position.x}, {map_position.y})"
            x0 = walk_x * block_width
            y0 = walk_y * block_height
            x1 = x0 + block_width - 1
            y1 = y0 + block_height - 1
            draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255, 130), width=2)
            _draw_centered_label(draw, font, label, x0, y0, x1, y1)


def _draw_centered_label(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    label: str,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> None:
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
    except AttributeError:
        bbox = (0, 0, len(label) * 6, 10)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    left = x0 + max(2, (x1 - x0 - text_width) // 2)
    top = y0 + max(2, (y1 - y0 - text_height) // 2)
    draw.rectangle((left - 2, top - 2, left + text_width + 2, top + text_height + 2), fill=TEXT_BG)
    draw.text((left, top), label, fill=TEXT_FILL, font=font)


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _to_rows(matrix: Any) -> list[list[int]]:
    if hasattr(matrix, "tolist"):
        matrix = matrix.tolist()

    if isinstance(matrix, memoryview):
        matrix = matrix.tolist()

    if not isinstance(matrix, Sequence) or isinstance(matrix, (bytes, bytearray, str)):
        return [[_to_int(matrix)]]

    rows: list[list[int]] = []
    for row in matrix:
        if isinstance(row, Sequence) and not isinstance(row, (bytes, bytearray, str)):
            rows.append([_to_int(value) for value in row])
        else:
            rows.append([_to_int(row)])
    return rows


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _position_to_grid_point(position: Any | None) -> GridPoint | None:
    if position is None:
        return None

    if isinstance(position, dict):
        try:
            return GridPoint(int(position["x"]), int(position["y"]))
        except (KeyError, TypeError, ValueError):
            return None

    try:
        return GridPoint(int(position.x), int(position.y))
    except (AttributeError, TypeError, ValueError):
        return None

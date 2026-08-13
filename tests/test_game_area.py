from pokemon_agent.vision.game_area import format_game_area_collision_watch, format_game_area_watch


class FakeGameAreaEnvironment:
    def game_area(self) -> list[list[int]]:
        return [[1, 2, 3], [4, 5, 6]]

    def game_area_collision(self) -> list[list[int]]:
        return [[0, 1, 0], [1, 1, 0]]


class BrokenGameAreaEnvironment:
    def game_area(self) -> list[list[int]]:
        raise RuntimeError("not ready")

    def game_area_collision(self) -> list[list[int]]:
        raise RuntimeError("not ready")


def test_format_game_area_watch() -> None:
    text = format_game_area_watch(FakeGameAreaEnvironment())

    assert "pyboy.game_area()" in text
    assert "shape: 2 rows x 3 cols" in text
    assert "00:" in text


def test_format_game_area_collision_watch() -> None:
    text = format_game_area_collision_watch(FakeGameAreaEnvironment())

    assert "pyboy.game_area_collision()" in text
    assert "shape: 2 rows x 3 cols" in text
    assert " 1" in text


def test_format_game_area_unavailable_message() -> None:
    assert "game_area unavailable" in format_game_area_watch(BrokenGameAreaEnvironment())
    assert "game_area_collision unavailable" in format_game_area_collision_watch(BrokenGameAreaEnvironment())

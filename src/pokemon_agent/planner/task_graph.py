from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StoryTask:
    key: str
    label: str
    depends_on: tuple[str, ...] = ()


@dataclass
class TaskGraph:
    tasks: dict[str, StoryTask] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)

    def add(self, task: StoryTask) -> None:
        self.tasks[task.key] = task

    def mark_done(self, key: str) -> None:
        self.completed.add(key)

    def ready(self) -> list[StoryTask]:
        return [
            task
            for task in self.tasks.values()
            if task.key not in self.completed
            and all(dependency in self.completed for dependency in task.depends_on)
        ]


def pokemon_red_story_graph() -> TaskGraph:
    graph = TaskGraph()
    graph.add(StoryTask("leave_home", "Leave the player's house"))
    graph.add(StoryTask("meet_oak", "Meet Professor Oak", depends_on=("leave_home",)))
    graph.add(StoryTask("get_starter", "Choose a starter Pokemon", depends_on=("meet_oak",)))
    graph.add(StoryTask("win_rival_1", "Win the first rival battle", depends_on=("get_starter",)))
    graph.add(StoryTask("reach_viridian", "Reach Viridian City", depends_on=("win_rival_1",)))
    graph.add(StoryTask("get_parcel", "Collect Oak's Parcel", depends_on=("reach_viridian",)))
    graph.add(StoryTask("deliver_parcel", "Deliver Oak's Parcel", depends_on=("get_parcel",)))
    graph.add(StoryTask("earn_badges", "Earn all eight badges", depends_on=("deliver_parcel",)))
    graph.add(StoryTask("beat_elite_four", "Beat the Elite Four", depends_on=("earn_badges",)))
    graph.add(StoryTask("beat_champion", "Beat the champion", depends_on=("beat_elite_four",)))
    return graph

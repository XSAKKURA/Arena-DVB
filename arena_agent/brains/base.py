"""Контракт между драйвером матча и стратегией игры.

Мозгу задают один вопрос — *дано это состояние, что отправляешь?* — и ему
разрешено ответить «пока ничего». Именно этот второй ответ заставляет работать
одновременные игры: в каратеке или трёх фронтах нет `yourTurn`, есть только флаг
`picked`/`submitted`, и знает об этом именно мозг.

Мозг живёт на матч, а не на процесс: драйвер создаёт свежий, когда садится за
стол, поэтому всё, что мозг помнит, ограничено этой партией и выбрасывается
вместе с ней. Память между матчами идёт через `ctx.store`.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("arena.brain")


@dataclass
class Context:
    """Всё, что может понадобиться мозгу, кроме самого состояния игры."""

    code: str
    game: str
    seat: int | None = None
    opponents: list[dict] = field(default_factory=list)
    store: Any = None
    rng: random.Random = field(default_factory=random.Random)
    think_seconds: float = 3.0
    pace: str = "live"
    mode: str = "ranked"
    deadline_at: float | None = None
    meta: dict = field(default_factory=dict)

    @property
    def opponent_name(self) -> str:
        for participant in self.opponents:
            name = participant.get("name")
            if name:
                return str(name)
        for participant in self.opponents:
            if participant.get("kind") == "human":
                return "a human"
        return "?"

    @property
    def vs_human(self) -> bool:
        return any(p.get("kind") == "human" for p in self.opponents)

    @property
    def vs_bot(self) -> bool:
        return any(p.get("kind") == "bot" for p in self.opponents)

    def budget(self) -> float:
        """Сколько секунд поиск может сжечь на этот ход, с оглядкой на дедлайн."""
        allowance = self.think_seconds
        if self.deadline_at:
            left = self.deadline_at - time.time()
            allowance = min(allowance, max(0.2, left * 0.4))
        return max(0.05, allowance)


class Brain:
    """Базовый класс. Наследники переопределяют `choose` и, по желанию, хуки."""

    game = ""

    def choose(self, state: dict, ctx: Context) -> dict | None:
        """Ход для отправки или None, если сейчас не наш ход либо делать нечего."""
        raise NotImplementedError

    def on_event(self, event: dict, ctx: Context) -> None:
        """Вызывается для каждого события из почтового ящика, по порядку."""

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        """Необязательная строка в чат стола по окончании матча (на английском)."""
        return None

    def greeting(self, ctx: Context) -> str | None:
        """Необязательная строка, когда мы садимся за стол (на английском)."""
        return None

    # -- помощники, общие для большинства мозгов ---------------------------

    @staticmethod
    def my_turn(state: dict) -> bool:
        """`yourTurn` — общий для всей арены ответ, он есть у каждой пошаговой
        игры. Его отсутствие означает одновременную игру, где мозг вместо этого
        смотрит собственный флаг отправки."""
        return bool(state.get("yourTurn"))


_REGISTRY: dict[str, type[Brain]] = {}


def register(cls: type[Brain]) -> type[Brain]:
    if not cls.game:
        raise ValueError(f"{cls.__name__} has no game id")
    _REGISTRY[cls.game] = cls
    return cls


def brain_for(game: str) -> Brain | None:
    cls = _REGISTRY.get(game)
    if cls is None:
        return None
    return cls()


def known_games() -> list[str]:
    return sorted(_REGISTRY)

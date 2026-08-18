"""The contract between the match driver and a game's strategy.

A brain is asked one question — *given this state, what do you send?* — and is
allowed to answer "nothing yet". That second answer is what makes simultaneous
games work: in karateka or three fronts there is no `yourTurn`, only a
`picked`/`submitted` flag, and the brain is the thing that knows which.

Brains are per match, not per process: the driver builds a fresh one when it
sits down, so anything a brain remembers is scoped to that game and gets thrown
away with it. Cross-match memory goes through `ctx.store`.
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
    """Everything a brain may need that is not the game state itself."""

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
        """Seconds a search may burn on this move, clipped to the deadline."""
        allowance = self.think_seconds
        if self.deadline_at:
            left = self.deadline_at - time.time()
            allowance = min(allowance, max(0.2, left * 0.4))
        return max(0.05, allowance)


class Brain:
    """Base class. Subclasses override `choose`, and optionally the hooks."""

    game = ""

    def choose(self, state: dict, ctx: Context) -> dict | None:
        """The move to send, or None when it is not our move / nothing to do."""
        raise NotImplementedError

    def on_event(self, event: dict, ctx: Context) -> None:
        """Called for every event drained from the mailbox, in order."""

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        """Optional line to post to the table chat when the match ends."""
        return None

    def greeting(self, ctx: Context) -> str | None:
        """Optional line to post when we sit down."""
        return None

    # -- helpers shared by most brains ------------------------------------

    @staticmethod
    def my_turn(state: dict) -> bool:
        """`yourTurn` is the arena-wide answer and every turn-based game has
        it. Absent means a simultaneous game, where the brain checks its own
        submitted flag instead."""
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

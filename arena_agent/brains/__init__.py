"""Strategy modules, one per game.

Importing this package registers every brain. `brain_for(game)` then hands the
match driver a fresh instance, or None for a game we do not claim to play — and
the runner refuses to sit down at a game it has no brain for, which is the
arena's own advice: play three games well rather than seventeen badly.
"""

from . import (  # noqa: F401  (imported for the side effect of registering)
    artillery,
    bulls,
    cards,
    checkers_brain,
    chess_brain,
    coop,
    dotsboxes,
    fifteen,
    gomoku,
    reversi,
    rule,
    seabattle,
    strategy_games,
    tanks,
)
from .base import Brain, Context, brain_for, known_games, register

__all__ = ["Brain", "Context", "brain_for", "known_games", "register"]

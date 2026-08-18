"""Модули стратегий, по одному на игру.

Импорт этого пакета регистрирует все мозги. `brain_for(game)` затем выдаёт
драйверу матча свежий экземпляр или None для игры, играть в которую мы не
беремся, — а runner отказывается садиться за игру, под которую мозга нет. Это
совет самой арены: лучше три игры хорошо, чем семнадцать плохо.
"""

from . import (  # noqa: F401  (импортируются ради побочного эффекта регистрации)
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

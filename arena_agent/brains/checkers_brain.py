"""Русские шашки.

Арена публикует в `legal_moves` полные цепочки взятий, поэтому отправляем мы
всегда что-то из этого списка. Собственный генератор нужен для перебора ниже
корня, — и поскольку его можно сверять со списком арены на каждом ходу,
расхождение проявляется предупреждением в логе, а не загадочно плохой игрой.
"""

from __future__ import annotations

import logging

from ..engines.checkers_engine import (
    BLACK,
    WHITE,
    CheckersSearch,
    legal_moves,
    square_index,
)
from .base import Brain, Context, register

log = logging.getLogger("arena.brain.checkers")


@register
class CheckersBrain(Brain):
    game = "checkers"

    def __init__(self) -> None:
        self.mismatch_warned = False

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        board = state.get("board")
        published = state.get("legal_moves") or []
        if not board or len(board) != 64 or not published:
            return None

        side = WHITE if state.get("your_color") == "white" else BLACK
        allowed: dict[tuple, list[str]] = {}
        for entry in published:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not path:
                continue
            try:
                allowed[tuple(square_index(square) for square in path)] = list(path)
            except (ValueError, IndexError, KeyError):
                continue
        if not allowed:
            return None

        ours = {path for path, _ in legal_moves(list(board), side)}
        if ours != set(allowed) and not self.mismatch_warned:
            self.mismatch_warned = True
            log.warning(
                "генератор ходов в шашках расходится с ареной: у нас=%d у неё=%d (играем по её списку)",
                len(ours),
                len(allowed),
            )

        search = CheckersSearch()
        best = search.best_move(list(board), side, ctx.budget())
        if best is None or best not in allowed:
            # Списку арены доверяем больше, чем собственному поиску, всегда.
            best = next(iter(allowed))
        log.debug("шашки: узлов %d", search.nodes)
        return {"type": "move", "path": allowed[best]}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. Alpha-beta over the shashki rules with capture extensions, so the search never stops "
            "in the middle of an exchange; material, advancement and king mobility in the evaluation."
        )

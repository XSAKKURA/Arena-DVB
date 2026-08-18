"""Shashki.

The arena publishes complete capture chains in `legal_moves`, so what we send
is always taken from that list. Our own generator exists to search below the
root — and because it can be checked against the arena's list on every move,
a disagreement shows up in the log as a warning rather than as mysteriously
bad play.
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
                "checkers move generator disagrees with the arena: ours=%d theirs=%d (playing from theirs)",
                len(ours),
                len(allowed),
            )

        search = CheckersSearch()
        best = search.best_move(list(board), side, ctx.budget())
        if best is None or best not in allowed:
            # Trust the arena's list over our own search every time.
            best = next(iter(allowed))
        log.debug("checkers: %d nodes", search.nodes)
        return {"type": "move", "path": allowed[best]}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. Alpha-beta over the shashki rules with capture extensions, so the search never stops "
            "in the middle of an exchange; material, advancement and king mobility in the evaluation."
        )

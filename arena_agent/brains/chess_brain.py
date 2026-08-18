"""Chess.

The arena publishes the full legal move list, so the only job is choosing. We
choose with a UCI engine when the box has one and with the built-in alpha-beta
otherwise — and either way the move that goes out is intersected with the
arena's own list, so a disagreement between our rules and theirs can only cost
strength, never a rejected move.
"""

from __future__ import annotations

import logging

from ..engines import uci
from ..engines.chess_engine import Position, Search, dict_to_move, move_to_dict, square_index
from .base import Brain, Context, register

log = logging.getLogger("arena.brain.chess")

# A small book, purely so that our openings are not the same game every time.
OPENING_BOOK: dict[str, list[str]] = {
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1": ["e2e4", "d2d4", "c2c4", "g1f3"],
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1": ["c7c5", "e7e5", "e7e6", "c7c6"],
    "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1": ["g8f6", "d7d5", "e7e6"],
}


@register
class ChessBrain(Brain):
    game = "chess"

    def __init__(self) -> None:
        self.moves_played = 0

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        fen = state.get("fen")
        published = state.get("legal_moves") or []
        if not fen or not published:
            return None

        position = Position(fen)
        allowed: list[tuple] = []
        for entry in published:
            try:
                allowed.append(dict_to_move(entry))
            except (KeyError, ValueError, IndexError):
                continue
        if not allowed:
            return None

        book = self._book_move(position, allowed, ctx)
        if book:
            return book

        engine = uci.shared_engine()
        if engine:
            answer = engine.best_move(fen, ctx.budget())
            move = self._parse_uci(answer, allowed)
            if move:
                self.moves_played += 1
                return move_to_dict(move)

        search = Search(position)
        best = search.best_move(ctx.budget(), allowed=allowed)
        if best is None:
            return dict(published[0])
        self.moves_played += 1
        log.debug("chess: %d nodes, playing %s", search.nodes, move_to_dict(best))
        return move_to_dict(best)

    def _book_move(self, position: Position, allowed: list[tuple], ctx: Context) -> dict | None:
        options = OPENING_BOOK.get(position.fen())
        if not options:
            return None
        ctx.rng.shuffle(options)
        for text in options:
            move = self._parse_uci(text, allowed)
            if move:
                return move_to_dict(move)
        return None

    @staticmethod
    def _parse_uci(text: str | None, allowed: list[tuple]) -> tuple | None:
        if not text or len(text) < 4:
            return None
        try:
            origin = square_index(text[0:2])
            target = square_index(text[2:4])
        except (ValueError, IndexError):
            return None
        promotion = text[4] if len(text) > 4 else None
        for move in allowed:
            if move[0] == origin and move[1] == target:
                if promotion and move[2] and move[2] != promotion:
                    continue
                return move
        return None

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        result = (state.get("result") or {}).get("outcome")
        engine = "a UCI engine" if uci.shared_engine() else "my own alpha-beta with quiescence and a transposition table"
        return (
            f"Good game ({result or 'no result'}). I chose with {engine}, always intersected against the arena's "
            f"legal-move list. {self.moves_played} moves from me. What were you searching with?"
        )

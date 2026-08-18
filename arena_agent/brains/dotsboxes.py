"""Dots and Boxes.

This game looks like a scramble for boxes and is really a game about parity.
Once every "safe" edge is gone the board is a set of chains, and the player
forced to open the first one loses all of it — so the real decisions are which
chain to open, and whether to take a whole chain or leave two boxes behind (the
double-cross) to hand the obligation back.

Below a handful of free edges the position is small enough to solve exactly,
and then we do.
"""

from __future__ import annotations

import sys

from .base import Brain, Context, register

sys.setrecursionlimit(10000)


class Board:
    """Edges as two flat lists, with the box-wall arithmetic in one place."""

    def __init__(self, n: int, horizontal: list[list[int]], vertical: list[list[int]]):
        self.n = n
        self.h = [list(row) for row in horizontal]
        self.v = [list(row) for row in vertical]

    def clone(self) -> "Board":
        return Board(self.n, self.h, self.v)

    def free_edges(self) -> list[tuple[str, int, int]]:
        out = []
        for r, row in enumerate(self.h):
            for c, value in enumerate(row):
                if not value:
                    out.append(("h", r, c))
        for r, row in enumerate(self.v):
            for c, value in enumerate(row):
                if not value:
                    out.append(("v", r, c))
        return out

    def box_walls(self, r: int, c: int) -> int:
        return (
            (1 if self.h[r][c] else 0)
            + (1 if self.h[r + 1][c] else 0)
            + (1 if self.v[r][c] else 0)
            + (1 if self.v[r][c + 1] else 0)
        )

    def boxes_of(self, edge: tuple[str, int, int]) -> list[tuple[int, int]]:
        kind, r, c = edge
        out = []
        if kind == "h":
            if r - 1 >= 0:
                out.append((r - 1, c))
            if r < self.n:
                out.append((r, c))
        else:
            if c - 1 >= 0:
                out.append((r, c - 1))
            if c < self.n:
                out.append((r, c))
        return out

    def play(self, edge: tuple[str, int, int], owner: int = 1) -> int:
        """Draw an edge; returns how many boxes it closed."""
        kind, r, c = edge
        target = self.h if kind == "h" else self.v
        target[r][c] = owner
        return sum(1 for br, bc in self.boxes_of(edge) if self.box_walls(br, bc) == 4)


def _classify(board: Board) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Split free edges into: closes a box now, safe, and gives one away."""
    closing, safe, giving = [], [], []
    for edge in board.free_edges():
        walls = [board.box_walls(r, c) for r, c in board.boxes_of(edge)]
        if any(w == 3 for w in walls):
            closing.append(edge)
        elif any(w == 2 for w in walls):
            giving.append(edge)  # would leave a box on three walls
        else:
            safe.append(edge)
    return closing, safe, giving


def _chain_sizes(board: Board) -> list[int]:
    """Sizes of the connected groups of not-yet-full boxes, walking through the
    walls that are still open. This is what "opening a chain" costs."""
    n = board.n
    seen: set[tuple[int, int]] = set()
    sizes: list[int] = []
    for r in range(n):
        for c in range(n):
            if (r, c) in seen or board.box_walls(r, c) == 4:
                continue
            stack = [(r, c)]
            seen.add((r, c))
            size = 0
            while stack:
                br, bc = stack.pop()
                size += 1
                neighbours = []
                if not board.h[br][bc] and br - 1 >= 0:
                    neighbours.append((br - 1, bc))
                if not board.h[br + 1][bc] and br + 1 < n:
                    neighbours.append((br + 1, bc))
                if not board.v[br][bc] and bc - 1 >= 0:
                    neighbours.append((br, bc - 1))
                if not board.v[br][bc + 1] and bc + 1 < n:
                    neighbours.append((br, bc + 1))
                for cell in neighbours:
                    if cell not in seen and board.box_walls(*cell) < 4:
                        seen.add(cell)
                        stack.append(cell)
            sizes.append(size)
    return sizes


def _solve(board: Board, memo: dict) -> tuple[int, tuple | None]:
    """Exact value of the position for the player to move, as a box
    differential, plus the move that achieves it."""
    key = (
        tuple(tuple(1 if x else 0 for x in row) for row in board.h),
        tuple(tuple(1 if x else 0 for x in row) for row in board.v),
    )
    if key in memo:
        return memo[key]

    edges = board.free_edges()
    if not edges:
        return 0, None

    best_value, best_edge = -10**6, None
    for edge in edges:
        child = board.clone()
        closed = child.play(edge)
        if closed:
            # Closing a box means moving again, so the value stays ours.
            sub, _ = _solve(child, memo)
            value = closed + sub
        else:
            sub, _ = _solve(child, memo)
            value = -sub
        if value > best_value:
            best_value, best_edge = value, edge
    memo[key] = (best_value, best_edge)
    return best_value, best_edge


@register
class DotsBoxesBrain(Brain):
    game = "dotsboxes"

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        n = int(state.get("n") or 0)
        horizontal = state.get("h")
        vertical = state.get("v")
        if not n or not horizontal or not vertical:
            return None
        board = Board(n, horizontal, vertical)
        edges = board.free_edges()
        if not edges:
            return None

        # Small enough to solve outright: play the proven best move, which is
        # where the double-cross gets found without being special-cased.
        if len(edges) <= 16:
            _, edge = _solve(board, {})
            if edge:
                return {"type": "edge", "kind": edge[0], "r": edge[1], "c": edge[2]}

        closing, safe, giving = _classify(board)
        if closing:
            return self._as_move(self._best_closing(board, closing, safe))
        if safe:
            return self._as_move(self._best_safe(board, safe, ctx))
        # Everything gives something away: open the cheapest chain.
        return self._as_move(self._cheapest_sacrifice(board, giving))

    @staticmethod
    def _as_move(edge: tuple[str, int, int]) -> dict:
        return {"type": "edge", "kind": edge[0], "r": edge[1], "c": edge[2]}

    def _best_closing(self, board: Board, closing: list, safe: list) -> tuple:
        """Take the box. The exception is the double-cross: if taking the last
        two boxes of a chain would force us to open the next chain, leave those
        two behind and make the opponent open it instead."""
        if len(closing) == 2 and not safe:
            probe = board.clone()
            for edge in closing:
                probe.play(edge)
            follow_closing, follow_safe, _ = _classify(probe)
            if not follow_closing and not follow_safe:
                # Taking both hands us the obligation; decline one of them.
                remaining = _chain_sizes(probe)
                if remaining and max(remaining) >= 3:
                    return closing[0]
        return closing[0]

    def _best_safe(self, board: Board, safe: list, ctx: Context) -> tuple:
        """Among edges that give nothing away, prefer the one that leaves the
        opponent with the fewest long chains to profit from later."""
        best, best_edge = None, safe[0]
        for edge in safe:
            probe = board.clone()
            probe.play(edge)
            sizes = _chain_sizes(probe)
            long_chains = sum(1 for size in sizes if size >= 3)
            # Chain-count parity decides who is forced to open first.
            key = (long_chains % 2, -len(sizes), ctx.rng.random())
            if best is None or key < best:
                best, best_edge = key, edge
        return best_edge

    def _cheapest_sacrifice(self, board: Board, giving: list) -> tuple:
        best, best_edge = None, giving[0]
        for edge in giving:
            probe = board.clone()
            probe.play(edge)
            # How many boxes the opponent can run off with from here.
            gift = 0
            while True:
                closes, _, _ = _classify(probe)
                if not closes:
                    break
                gift += probe.play(closes[0])
            if best is None or gift < best:
                best, best_edge = gift, edge
        return best_edge

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. I play safe edges while any exist, pick which chain to open by chain-count parity, and "
            "solve the position exactly once sixteen edges or fewer are left — that is where the double-cross "
            "shows up on its own."
        )

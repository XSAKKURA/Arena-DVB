"""Five in a Row on 15x15.

The arena publishes no legal-move list for gomoku, but every empty cell is
legal, so the work is entirely in choosing. Two layers do it:

* a **tactical layer** that never misses a forced sequence — win now, block a
  win, make a four, answer a four, answer an open three;
* a **pattern-scored alpha-beta** with iterative deepening for everything else,
  searching only cells near existing stones because nothing else can matter.
"""

from __future__ import annotations

import re
import time

from .base import Brain, Context, register

SIZE = 15
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))

# Patterns are read on a line where '1' is the player scored, '2' is the
# opponent *or a wall*, and '0' is empty. Padding every line with '2' makes the
# board edge behave exactly like a blocking stone, which it is.
_PATTERN_SCORES: list[tuple[str, int]] = [
    ("11111", 10_000_000),
    ("011110", 1_000_000),
    ("011112", 120_000),
    ("211110", 120_000),
    ("10111", 120_000),
    ("11011", 120_000),
    ("11101", 120_000),
    ("01110", 20_000),
    ("010110", 18_000),
    ("011010", 18_000),
    ("001112", 1_200),
    ("211100", 1_200),
    ("011012", 1_200),
    ("210110", 1_200),
    ("10011", 1_000),
    ("11001", 1_000),
    ("10101", 1_000),
    ("00110", 220),
    ("01100", 220),
    ("01010", 200),
    ("010012", 60),
    ("210100", 60),
    ("10001", 60),
]
_COMPILED = [(re.compile("(?=%s)" % pattern), score) for pattern, score in _PATTERN_SCORES]

WIN_SCORE = 10_000_000


def _lines(board: list[int], size: int) -> list[list[int]]:
    """Every row, column and diagonal of length >= 5, as lists of cell values."""
    out: list[list[int]] = []
    for r in range(size):
        out.append([board[r * size + c] for c in range(size)])
    for c in range(size):
        out.append([board[r * size + c] for r in range(size)])
    for offset in range(-size + 5, size - 4):
        diagonal = []
        anti = []
        for r in range(size):
            c = r + offset
            if 0 <= c < size:
                diagonal.append(board[r * size + c])
            c2 = size - 1 - r + offset
            if 0 <= c2 < size:
                anti.append(board[r * size + c2])
        if len(diagonal) >= 5:
            out.append(diagonal)
        if len(anti) >= 5:
            out.append(anti)
    return out


def _score_for(board: list[int], size: int, me: int) -> int:
    total = 0
    for line in _lines(board, size):
        text = "2" + "".join("1" if v == me else ("0" if v == 0 else "2") for v in line) + "2"
        for pattern, score in _COMPILED:
            found = len(pattern.findall(text))
            if found:
                total += found * score
    return total


class GomokuPosition:
    """A mutable board with make/undo, so the search does not copy 225 ints per
    node."""

    def __init__(self, board: list[int], size: int, me: int, opponent: int):
        self.board = list(board)
        self.size = size
        self.me = me
        self.opponent = opponent

    def empty(self, index: int) -> bool:
        return self.board[index] == 0

    def place(self, index: int, player: int) -> None:
        self.board[index] = player

    def undo(self, index: int) -> None:
        self.board[index] = 0

    def evaluate(self) -> int:
        # A shade over 1.0 on the defensive term: in gomoku the side that has
        # to answer a threat has already lost the initiative.
        return _score_for(self.board, self.size, self.me) - int(
            _score_for(self.board, self.size, self.opponent) * 1.08
        )

    def wins(self, player: int) -> bool:
        target = str(player)
        for line in _lines(self.board, self.size):
            run = 0
            for value in line:
                run = run + 1 if value == player else 0
                if run >= 5:
                    return True
        del target
        return False

    def candidates(self, radius: int = 2, limit: int = 14) -> list[int]:
        """Empty cells within `radius` of a stone. On an empty board, the
        centre — anywhere else is strictly worse and symmetric anyway."""
        size = self.size
        occupied = [i for i, v in enumerate(self.board) if v != 0]
        if not occupied:
            return [(size // 2) * size + size // 2]
        near: set[int] = set()
        for index in occupied:
            r, c = divmod(index, size)
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < size and 0 <= cc < size:
                        candidate = rr * size + cc
                        if self.board[candidate] == 0:
                            near.add(candidate)
        scored = []
        for index in near:
            self.board[index] = self.me
            mine = _score_for(self.board, size, self.me)
            self.board[index] = self.opponent
            theirs = _score_for(self.board, size, self.opponent)
            self.board[index] = 0
            scored.append((mine + theirs, index))
        scored.sort(reverse=True)
        return [index for _, index in scored[:limit]]


def _immediate(position: GomokuPosition, player: int) -> list[int]:
    """Cells where `player` completes five right now."""
    out = []
    for index in position.candidates(radius=2, limit=60):
        position.place(index, player)
        if position.wins(player):
            out.append(index)
        position.undo(index)
    return out


def _search(
    position: GomokuPosition,
    depth: int,
    alpha: int,
    beta: int,
    maximising: bool,
    deadline: float,
) -> int:
    if time.time() > deadline:
        raise TimeoutError
    if depth == 0:
        return position.evaluate()

    player = position.me if maximising else position.opponent
    moves = position.candidates(limit=10 if depth > 2 else 8)
    if not moves:
        return position.evaluate()

    if maximising:
        best = -(WIN_SCORE * 10)
        for index in moves:
            position.place(index, player)
            if position.wins(player):
                value = WIN_SCORE * (depth + 1)
            else:
                value = _search(position, depth - 1, alpha, beta, False, deadline)
            position.undo(index)
            best = max(best, value)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best

    best = WIN_SCORE * 10
    for index in moves:
        position.place(index, player)
        if position.wins(player):
            value = -WIN_SCORE * (depth + 1)
        else:
            value = _search(position, depth - 1, alpha, beta, True, deadline)
        position.undo(index)
        best = min(best, value)
        beta = min(beta, value)
        if alpha >= beta:
            break
    return best


@register
class GomokuBrain(Brain):
    game = "gomoku"

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        board = state.get("board") or []
        size = int(state.get("size") or SIZE)
        if len(board) != size * size:
            return None

        me = ctx.seat
        symbols = state.get("symbols") or {}
        if me is None:
            return None
        opponent = 0
        for seat in symbols:
            if str(seat) != str(me):
                opponent = int(seat)
        if not opponent:
            for value in board:
                if value not in (0, me):
                    opponent = value
                    break
        position = GomokuPosition(board, size, int(me), int(opponent or -1))

        # 1. Win now.
        wins = _immediate(position, position.me)
        if wins:
            return self._as_move(wins[0], size)
        # 2. Stop them winning now.
        blocks = _immediate(position, position.opponent)
        if blocks:
            return self._as_move(blocks[0], size)

        # 3. Search everything else, going deeper for as long as we are allowed.
        deadline = time.time() + ctx.budget()
        best_move = position.candidates(limit=1)[0]
        for depth in range(2, 9):
            try:
                alpha, beta = -(WIN_SCORE * 10), WIN_SCORE * 10
                local_best, local_move = -(WIN_SCORE * 10), None
                for index in position.candidates(limit=12):
                    position.place(index, position.me)
                    if position.wins(position.me):
                        value = WIN_SCORE * (depth + 1)
                    else:
                        value = _search(position, depth - 1, alpha, beta, False, deadline)
                    position.undo(index)
                    if value > local_best:
                        local_best, local_move = value, index
                    alpha = max(alpha, value)
                if local_move is not None:
                    best_move = local_move
                if local_best >= WIN_SCORE:
                    break
            except TimeoutError:
                break
        return self._as_move(best_move, size)

    @staticmethod
    def _as_move(index: int, size: int) -> dict:
        r, c = divmod(index, size)
        return {"type": "move", "r": r, "c": c}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. I run a tactical check first (win, block, four, open three) and then an "
            "iterative-deepening alpha-beta over pattern scores, searching only cells within two of a stone."
        )

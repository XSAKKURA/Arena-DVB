"""Shashki — draughts in the Russian rule set the arena actually uses.

The rules that make this variant its own game, and that a generic draughts
engine gets wrong: men capture backwards as well as forwards, kings are
long-range in both movement and capture, capturing is mandatory but the
*choice* of capture is free (no majority rule), a chain is one move and must be
finished, and a man that reaches the last rank mid-chain becomes a king and
carries on capturing immediately.

Captured pieces stay on the board until the chain ends — they block, and they
cannot be jumped a second time.

Board indexing matches the arena: index = r*8 + c, r=0 is the eighth rank and
c=0 is file a, so "c3" is 42.
"""

from __future__ import annotations

import time

WHITE, BLACK = "white", "black"
DIAGONALS = ((-1, -1), (-1, 1), (1, -1), (1, 1))

MAN_VALUE = 100
KING_VALUE = 340


def square_name(index: int) -> str:
    return "abcdefgh"[index % 8] + str(8 - index // 8)


def square_index(name: str) -> int:
    return (8 - int(name[1])) * 8 + "abcdefgh".index(name[0])


def is_white(piece: str | None) -> bool:
    return piece in ("w", "W")


def is_king(piece: str | None) -> bool:
    return piece in ("W", "B")


def side_of(piece: str | None) -> str | None:
    if piece in ("w", "W"):
        return WHITE
    if piece in ("b", "B"):
        return BLACK
    return None


def _promotion_rank(side: str) -> int:
    # White marches towards rank 8, which is row 0 in this indexing.
    return 0 if side == WHITE else 7


def _forward(side: str) -> int:
    return -1 if side == WHITE else 1


def _walk(index: int, dr: int, dc: int, steps: int = 1) -> int | None:
    r, c = divmod(index, 8)
    r += dr * steps
    c += dc * steps
    if 0 <= r < 8 and 0 <= c < 8:
        return r * 8 + c
    return None


def _capture_chains(
    board: list, index: int, piece: str, side: str, captured: frozenset, path: tuple
) -> list[tuple[tuple, frozenset, str]]:
    """Every way the piece at `index` can continue capturing. Returns
    (path, captured squares, final piece) for each completed chain."""
    results: list[tuple[tuple, frozenset, str]] = []
    king = is_king(piece)

    for dr, dc in DIAGONALS:
        if king:
            # Slide over empty squares, take the first piece found if it is an
            # uncaptured enemy, then land anywhere free beyond it.
            step = 1
            victim = None
            while True:
                square = _walk(index, dr, dc, step)
                if square is None:
                    break
                occupant = board[square]
                if occupant:
                    if square in captured or side_of(occupant) == side:
                        break
                    victim = square
                    break
                step += 1
            if victim is None:
                continue
            landing_step = step + 1
            while True:
                landing = _walk(index, dr, dc, landing_step)
                if landing is None:
                    break
                if board[landing] or landing in captured:
                    break
                results.extend(
                    _continue(board, landing, piece, side, captured | {victim}, path + (landing,))
                )
                landing_step += 1
        else:
            over = _walk(index, dr, dc, 1)
            landing = _walk(index, dr, dc, 2)
            if over is None or landing is None:
                continue
            occupant = board[over]
            if not occupant or over in captured or side_of(occupant) == side:
                continue
            if board[landing] or landing in captured:
                continue
            # A man crowning mid-chain keeps capturing, now as a king.
            became = piece
            if landing // 8 == _promotion_rank(side):
                became = "W" if side == WHITE else "B"
            results.extend(
                _continue(board, landing, became, side, captured | {over}, path + (landing,))
            )

    return results


def _continue(
    board: list, index: int, piece: str, side: str, captured: frozenset, path: tuple
) -> list[tuple[tuple, frozenset, str]]:
    onward = _capture_chains(board, index, piece, side, captured, path)
    if onward:
        return onward
    return [(path, captured, piece)]


def legal_moves(board: list, side: str) -> list[tuple[tuple, list]]:
    """Every legal move as (path of square indexes, resulting board).

    Capturing is mandatory: if any capture exists, quiet moves are not legal.
    """
    captures: list[tuple[tuple, list]] = []
    quiet: list[tuple[tuple, list]] = []

    for index, piece in enumerate(board):
        if side_of(piece) != side:
            continue
        working = list(board)
        working[index] = None  # the piece is in the air for the whole chain
        for path, taken, final_piece in _capture_chains(
            working, index, piece, side, frozenset(), (index,)
        ):
            if len(path) < 2:
                continue
            result = list(board)
            result[index] = None
            for square in taken:
                result[square] = None
            landing = path[-1]
            if not is_king(final_piece) and landing // 8 == _promotion_rank(side):
                final_piece = "W" if side == WHITE else "B"
            result[landing] = final_piece
            captures.append((path, result))

    if captures:
        return captures

    for index, piece in enumerate(board):
        if side_of(piece) != side:
            continue
        if is_king(piece):
            for dr, dc in DIAGONALS:
                step = 1
                while True:
                    square = _walk(index, dr, dc, step)
                    if square is None or board[square]:
                        break
                    result = list(board)
                    result[index] = None
                    result[square] = piece
                    quiet.append(((index, square), result))
                    step += 1
        else:
            direction = _forward(side)
            for dc in (-1, 1):
                square = _walk(index, direction, dc, 1)
                if square is None or board[square]:
                    continue
                result = list(board)
                result[index] = None
                promoted = square // 8 == _promotion_rank(side)
                result[square] = ("W" if side == WHITE else "B") if promoted else piece
                quiet.append(((index, square), result))

    return quiet


def evaluate(board: list, side: str) -> int:
    """Positive means `side` stands better."""
    score = 0
    for index, piece in enumerate(board):
        if not piece:
            continue
        owner = side_of(piece)
        r, c = divmod(index, 8)
        if is_king(piece):
            value = KING_VALUE
            # Kings want the middle, where more diagonals are long.
            value += 6 * (3 - max(abs(r - 3.5), abs(c - 3.5)))
        else:
            value = MAN_VALUE
            # Advancement, counted towards each side's own promotion rank.
            advance = (7 - r) if owner == WHITE else r
            value += advance * 7
            # The back rank is a defensive asset worth keeping for a while.
            if (owner == WHITE and r == 7) or (owner == BLACK and r == 0):
                value += 12
            if 2 <= c <= 5:
                value += 4
        score += value if owner == side else -value
    return int(score)


def other(side: str) -> str:
    return BLACK if side == WHITE else WHITE


class CheckersSearch:
    def __init__(self) -> None:
        self.nodes = 0
        self.deadline = 0.0

    def search(self, board: list, side: str, depth: int, alpha: int, beta: int) -> int:
        """Negamax: the score is always from the point of view of `side`."""
        self.nodes += 1
        if self.nodes % 512 == 0 and time.time() > self.deadline:
            raise TimeoutError

        moves = legal_moves(board, side)
        if not moves:
            # No pieces left, or nothing legal to play: this side has lost.
            return -1_000_000 - depth

        # Never take the evaluation in the middle of an exchange — extend
        # through forced captures instead.
        if depth <= 0 and not any(len(path) > 2 for path, _ in moves):
            return evaluate(board, side)

        best = -2_000_000
        for _, result in sorted(moves, key=lambda item: -len(item[0])):
            value = -self.search(result, other(side), depth - 1, -beta, -alpha)
            best = max(best, value)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best

    def best_move(self, board: list, side: str, seconds: float) -> tuple | None:
        self.deadline = time.time() + max(0.05, seconds)
        moves = legal_moves(board, side)
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0][0]

        best_path = moves[0][0]
        for depth in range(1, 20):
            try:
                alpha, beta = -2_000_000, 2_000_000
                local_best, local_path = -2_000_000, None
                # Longer captures first: they are usually good and prune well.
                ordered = sorted(moves, key=lambda item: -len(item[0]))
                for path, result in ordered:
                    value = -self.search(result, other(side), depth - 1, -beta, -alpha)
                    if value > local_best:
                        local_best, local_path = value, path
                    alpha = max(alpha, value)
                if local_path is not None:
                    best_path = local_path
            except TimeoutError:
                break
        return best_path


def path_to_move(path: tuple) -> dict:
    return {"type": "move", "path": [square_name(square) for square in path]}

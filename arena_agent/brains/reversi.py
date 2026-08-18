"""Реверси (Отелло) на доске 8x8.

Арена выдаёт нам `legal_moves`, так что правила — не наша забота, наша забота —
выбор. Количество фишек, как известно, до самого конца максимизировать не надо,
поэтому оценка складывается из углов, подвижности, фронтовых фишек и таблицы
клеток, а на подсчёт фишек переключается ровно тогда, когда позиция становится
достаточно маленькой, чтобы досчитать её точно.
"""

from __future__ import annotations

import time

from .base import Brain, Context, register

EMPTY, BLACK, WHITE = 0, 1, 2

SQUARE_WEIGHTS = [
    120, -20, 20, 5, 5, 20, -20, 120,
    -20, -40, -5, -5, -5, -5, -40, -20,
    20, -5, 15, 3, 3, 15, -5, 20,
    5, -5, 3, 3, 3, 3, -5, 5,
    5, -5, 3, 3, 3, 3, -5, 5,
    20, -5, 15, 3, 3, 15, -5, 20,
    -20, -40, -5, -5, -5, -5, -40, -20,
    120, -20, 20, 5, 5, 20, -20, 120,
]
CORNERS = (0, 7, 56, 63)
# Клетка по диагонали внутрь от угла: яд, пока угол пуст.
X_SQUARES = {0: 9, 7: 14, 56: 49, 63: 54}

_DIRECTIONS = (-9, -8, -7, -1, 1, 7, 8, 9)


def _neighbours(index: int, step: int) -> bool:
    """Остаётся ли шаг `step` из `index` на доске (без переноса через край)."""
    r, c = divmod(index, 8)
    dr, dc = divmod(step + 9, 8)
    dr, dc = dr - 1, dc - 1
    rr, cc = r + dr, c + dc
    return 0 <= rr < 8 and 0 <= cc < 8


def _flips(board: list[int], index: int, colour: int) -> list[int]:
    if board[index] != EMPTY:
        return []
    other = BLACK if colour == WHITE else WHITE
    gained: list[int] = []
    for step in _DIRECTIONS:
        run: list[int] = []
        current = index
        while True:
            if not _neighbours(current, step):
                run = []
                break
            current += step
            value = board[current]
            if value == other:
                run.append(current)
                continue
            if value == colour and run:
                break
            run = []
            break
        gained.extend(run)
    return gained


def legal_moves(board: list[int], colour: int) -> dict[int, list[int]]:
    out = {}
    for index in range(64):
        if board[index] != EMPTY:
            continue
        gained = _flips(board, index, colour)
        if gained:
            out[index] = gained
    return out


def _apply(board: list[int], index: int, colour: int, gained: list[int]) -> list[int]:
    new = list(board)
    new[index] = colour
    for cell in gained:
        new[cell] = colour
    return new


def _frontier(board: list[int], colour: int) -> int:
    """Фишки цвета `colour`, соседствующие с пустой клеткой. Чем меньше, тем
    лучше: именно их соперник может перевернуть."""
    count = 0
    for index in range(64):
        if board[index] != colour:
            continue
        for step in _DIRECTIONS:
            if _neighbours(index, step) and board[index + step] == EMPTY:
                count += 1
                break
    return count


def evaluate(board: list[int], me: int) -> int:
    other = BLACK if me == WHITE else WHITE
    empties = board.count(EMPTY)

    if empties == 0:
        mine, theirs = board.count(me), board.count(other)
        return 100_000 * (1 if mine > theirs else (-1 if theirs > mine else 0))

    positional = 0
    for index in range(64):
        value = board[index]
        if value == me:
            positional += SQUARE_WEIGHTS[index]
        elif value == other:
            positional -= SQUARE_WEIGHTS[index]

    # X-клетка ядовита только пока её угол ещё можно занять.
    for corner, x_square in X_SQUARES.items():
        if board[corner] == EMPTY:
            if board[x_square] == me:
                positional -= 30
            elif board[x_square] == other:
                positional += 30

    my_corners = sum(1 for c in CORNERS if board[c] == me)
    their_corners = sum(1 for c in CORNERS if board[c] == other)
    corner_term = 200 * (my_corners - their_corners)

    my_moves = len(legal_moves(board, me))
    their_moves = len(legal_moves(board, other))
    if my_moves + their_moves:
        mobility = 100 * (my_moves - their_moves) / (my_moves + their_moves)
    else:
        mobility = 0

    frontier = -8 * (_frontier(board, me) - _frontier(board, other))

    if empties <= 12:
        # Эндшпиль: результат в самом деле складывается из фишек.
        mine, theirs = board.count(me), board.count(other)
        return int(positional * 0.3 + corner_term + 12 * (mine - theirs) + mobility * 2)

    return int(positional + corner_term + mobility * 15 + frontier)


def _search(
    board: list[int],
    colour: int,
    me: int,
    depth: int,
    alpha: int,
    beta: int,
    passed: bool,
    deadline: float,
) -> int:
    if time.time() > deadline:
        raise TimeoutError
    if depth <= 0:
        return evaluate(board, me)

    moves = legal_moves(board, colour)
    other = BLACK if colour == WHITE else WHITE
    if not moves:
        if passed:
            return evaluate(board, me)
        return _search(board, other, me, depth, alpha, beta, True, deadline)

    # Сначала углы, X-клетки в конец: дешёвое упорядочивание, отсекающее многое.
    ordered = sorted(moves.items(), key=lambda kv: -SQUARE_WEIGHTS[kv[0]])
    maximising = colour == me

    if maximising:
        best = -10**9
        for index, gained in ordered:
            value = _search(
                _apply(board, index, colour, gained), other, me, depth - 1, alpha, beta, False, deadline
            )
            best = max(best, value)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best

    best = 10**9
    for index, gained in ordered:
        value = _search(
            _apply(board, index, colour, gained), other, me, depth - 1, alpha, beta, False, deadline
        )
        best = min(best, value)
        beta = min(beta, value)
        if alpha >= beta:
            break
    return best


@register
class ReversiBrain(Brain):
    game = "reversi"

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        board = state.get("board") or []
        if len(board) != 64:
            return None
        published = state.get("legal_moves") or []
        if not published:
            return {"type": "pass"}

        me = BLACK if state.get("your_color") == "black" else WHITE
        other = WHITE if me == BLACK else BLACK
        moves = legal_moves(board, me)
        # При расхождении с нашим генератором доверяем списку арены.
        allowed = {int(m["r"]) * 8 + int(m["c"]) for m in published if "r" in m and "c" in m}
        moves = {k: v for k, v in moves.items() if k in allowed} or {
            index: _flips(board, index, me) for index in allowed
        }
        if not moves:
            return {"type": "pass"}

        empties = board.count(EMPTY)
        deadline = time.time() + ctx.budget()
        best_index = max(moves, key=lambda i: SQUARE_WEIGHTS[i])

        # Когда дерево становится достаточно малым, досчитываем его до конца и
        # играем результат, а не оценку результата.
        max_depth = empties if empties <= 10 else 8
        for depth in range(2, max_depth + 1):
            try:
                alpha, beta = -10**9, 10**9
                local_best, local_move = -10**9, None
                for index, gained in sorted(moves.items(), key=lambda kv: -SQUARE_WEIGHTS[kv[0]]):
                    value = _search(
                        _apply(board, index, me, gained), other, me, depth - 1, alpha, beta, False, deadline
                    )
                    if value > local_best:
                        local_best, local_move = value, index
                    alpha = max(alpha, value)
                if local_move is not None:
                    best_index = local_move
            except TimeoutError:
                break

        r, c = divmod(best_index, 8)
        return {"type": "move", "r": r, "c": c}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. I evaluate corners, mobility and frontier discs rather than disc count, and switch to "
            "an exact solve once twelve squares or fewer are empty."
        )

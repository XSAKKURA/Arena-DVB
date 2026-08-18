"""Точки и квадраты.

Эта игра выглядит как драка за квадраты, а на деле она про чётность. Когда
кончаются все «безопасные» рёбра, доска превращается в набор цепочек, и игрок,
вынужденный вскрыть первую, отдаёт её целиком, — так что настоящие решения тут
это какую цепочку вскрывать и брать ли цепочку целиком или оставить два квадрата
(двойная жертва), чтобы вернуть обязанность ходить сопернику.

Когда свободных рёбер остаётся немного, позиция становится достаточно маленькой,
чтобы решить её точно, — тогда мы так и делаем.
"""

from __future__ import annotations

import sys

from .base import Brain, Context, register

sys.setrecursionlimit(10000)


class Board:
    """Рёбра двумя плоскими списками, вся арифметика стен квадрата — в одном месте."""

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
        """Провести ребро; возвращает, сколько квадратов оно закрыло."""
        kind, r, c = edge
        target = self.h if kind == "h" else self.v
        target[r][c] = owner
        return sum(1 for br, bc in self.boxes_of(edge) if self.box_walls(br, bc) == 4)


def _classify(board: Board) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Разбить свободные рёбра на: закрывает квадрат сейчас, безопасные, дарящие."""
    closing, safe, giving = [], [], []
    for edge in board.free_edges():
        walls = [board.box_walls(r, c) for r, c in board.boxes_of(edge)]
        if any(w == 3 for w in walls):
            closing.append(edge)
        elif any(w == 2 for w in walls):
            giving.append(edge)  # оставило бы квадрат с тремя стенами
        else:
            safe.append(edge)
    return closing, safe, giving


def _chain_sizes(board: Board) -> list[int]:
    """Размеры связных групп ещё не закрытых квадратов, если ходить сквозь ещё
    открытые стены. Это и есть цена «вскрытия цепочки»."""
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
    """Точная ценность позиции для того, чей ход, в виде разницы квадратов, и
    ход, который её достигает."""
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
            # Закрыв квадрат, ходишь снова, поэтому ценность остаётся нашей.
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

        # Достаточно мало, чтобы решить целиком: играем доказанный лучший ход —
        # именно так двойная жертва находится сама, без отдельного правила.
        if len(edges) <= 16:
            _, edge = _solve(board, {})
            if edge:
                return {"type": "edge", "kind": edge[0], "r": edge[1], "c": edge[2]}

        closing, safe, giving = _classify(board)
        if closing:
            return self._as_move(self._best_closing(board, closing, safe))
        if safe:
            return self._as_move(self._best_safe(board, safe, ctx))
        # Всё что-то дарит: вскрываем самую дешёвую цепочку.
        return self._as_move(self._cheapest_sacrifice(board, giving))

    @staticmethod
    def _as_move(edge: tuple[str, int, int]) -> dict:
        return {"type": "edge", "kind": edge[0], "r": edge[1], "c": edge[2]}

    def _best_closing(self, board: Board, closing: list, safe: list) -> tuple:
        """Забрать квадрат. Исключение — двойная жертва: если взятие двух
        последних квадратов цепочки вынудит нас вскрыть следующую, оставляем эти
        два и заставляем вскрывать соперника."""
        if len(closing) == 2 and not safe:
            probe = board.clone()
            for edge in closing:
                probe.play(edge)
            follow_closing, follow_safe, _ = _classify(probe)
            if not follow_closing and not follow_safe:
                # Взять оба значит получить обязанность ходить; от одного отказываемся.
                remaining = _chain_sizes(probe)
                if remaining and max(remaining) >= 3:
                    return closing[0]
        return closing[0]

    def _best_safe(self, board: Board, safe: list, ctx: Context) -> tuple:
        """Среди рёбер, которые ничего не дарят, предпочитаем то, что оставляет
        сопернику меньше длинных цепочек для последующей наживы."""
        best, best_edge = None, safe[0]
        for edge in safe:
            probe = board.clone()
            probe.play(edge)
            sizes = _chain_sizes(probe)
            long_chains = sum(1 for size in sizes if size >= 3)
            # Чётность числа цепочек решает, кто будет вынужден вскрывать первым.
            key = (long_chains % 2, -len(sizes), ctx.rng.random())
            if best is None or key < best:
                best, best_edge = key, edge
        return best_edge

    def _cheapest_sacrifice(self, board: Board, giving: list) -> tuple:
        best, best_edge = None, giving[0]
        for edge in giving:
            probe = board.clone()
            probe.play(edge)
            # Сколько квадратов соперник может отсюда унести.
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
            "Good game. This looks like a scramble for boxes and is really a game about chains and their "
            "parity — whoever is forced to open the first long one pays for it. Were you counting them?"
        )

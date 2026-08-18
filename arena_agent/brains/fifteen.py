"""Fifteen Puzzle.

Everyone gets the same shuffle and the first to report a solved board wins, so
the race is against the clock, not against the move count. Weighted A* over
Manhattan distance plus linear conflict finds a valid solution in milliseconds
where optimal search could take minutes — and the move count we report is the
real length of a solution we actually found, not an estimate.
"""

from __future__ import annotations

import heapq
import logging
import time

from .base import Brain, Context, register

log = logging.getLogger("arena.brain.fifteen")


def manhattan(board: tuple[int, ...], n: int) -> int:
    total = 0
    for index, tile in enumerate(board):
        if tile == 0:
            continue
        goal = tile - 1
        total += abs(index // n - goal // n) + abs(index % n - goal % n)
    return total


def linear_conflict(board: tuple[int, ...], n: int) -> int:
    """Two tiles in their goal row but the wrong way round cost two extra
    moves, because one has to step aside for the other."""
    extra = 0
    for row in range(n):
        goals = []
        for col in range(n):
            tile = board[row * n + col]
            if tile and (tile - 1) // n == row:
                goals.append((tile - 1) % n)
        extra += 2 * _inversions(goals)
    for col in range(n):
        goals = []
        for row in range(n):
            tile = board[row * n + col]
            if tile and (tile - 1) % n == col:
                goals.append((tile - 1) // n)
        extra += 2 * _inversions(goals)
    return extra


def _inversions(values: list[int]) -> int:
    return sum(
        1
        for i in range(len(values))
        for j in range(i + 1, len(values))
        if values[i] > values[j]
    )


def solve(board: list[int], n: int, seconds: float = 4.0) -> list[int] | None:
    """A sequence of tile values to slide, or None if we ran out of time.

    Weighted A*: the weight buys speed at the cost of optimality, and we start
    greedy and relax the weight only if the greedy pass somehow fails.
    """
    start = tuple(board)
    goal = tuple(list(range(1, n * n)) + [0])
    if start == goal:
        return []

    deadline = time.time() + seconds
    for weight in (3.0, 2.0, 1.5):
        found = _weighted_astar(start, goal, n, weight, deadline)
        if found is not None:
            return found
        if time.time() > deadline:
            break
    return None


def _weighted_astar(start, goal, n, weight, deadline) -> list[int] | None:
    heuristic = manhattan(start, n) + linear_conflict(start, n)
    queue = [(weight * heuristic, 0, start, None)]
    came_from: dict[tuple, tuple] = {start: (None, None)}
    best_cost = {start: 0}
    counter = 0

    while queue:
        counter += 1
        if counter % 4096 == 0 and time.time() > deadline:
            return None
        _, cost, state, _ = heapq.heappop(queue)
        if state == goal:
            return _rebuild(came_from, state)
        if cost > best_cost.get(state, 1 << 30):
            continue

        blank = state.index(0)
        row, col = divmod(blank, n)
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = row + dr, col + dc
            if not (0 <= rr < n and 0 <= cc < n):
                continue
            swap = rr * n + cc
            listed = list(state)
            moved_tile = listed[swap]
            listed[blank], listed[swap] = listed[swap], listed[blank]
            child = tuple(listed)
            new_cost = cost + 1
            if new_cost >= best_cost.get(child, 1 << 30):
                continue
            best_cost[child] = new_cost
            came_from[child] = (state, moved_tile)
            estimate = manhattan(child, n) + linear_conflict(child, n)
            heapq.heappush(queue, (new_cost + weight * estimate, new_cost, child, moved_tile))
    return None


def _rebuild(came_from: dict, state: tuple) -> list[int]:
    moves: list[int] = []
    while True:
        parent, tile = came_from.get(state, (None, None))
        if parent is None:
            break
        moves.append(tile)
        state = parent
    moves.reverse()
    return moves


def _placed(board: list[int], n: int) -> int:
    return sum(1 for index, tile in enumerate(board) if tile and tile == index + 1)


@register
class FifteenBrain(Brain):
    game = "fifteen"

    def __init__(self) -> None:
        self.reported = False
        self.solution_length: int | None = None

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if state.get("solvedMe") or self.reported:
            return None
        board = list(state.get("board") or [])
        n = int(state.get("n") or 4)
        if len(board) != n * n:
            return None

        moves = solve(board, n, seconds=min(6.0, max(1.0, ctx.budget() * 2)))
        if moves is None:
            # Could not finish in the time we had: say honestly how far the
            # board already is rather than claiming anything.
            log.warning("fifteen: no solution found in budget, reporting progress only")
            return {"type": "progress", "placed": _placed(board, n), "moves": 0}

        self.reported = True
        self.solution_length = len(moves)
        log.info("fifteen: solved in %d moves", len(moves))
        return {"type": "solved", "moves": len(moves)}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        if self.solution_length is None:
            return None
        return (
            f"Good game. Weighted A* over Manhattan distance plus linear conflict — it trades optimality for "
            f"speed, which is the right trade when the winner is whoever reports first. "
            f"My solution was {self.solution_length} moves."
        )

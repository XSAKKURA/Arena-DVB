"""Sea Battle on 10x10.

Two halves, and the arena is right that placement is one of them.

* **Placement** is uniformly random over legal fleets, which is the only
  placement with no pattern to learn. The one bias worth having is against
  clumping: a fleet packed into a quadrant dies to a shooter who finds the
  quadrant.
* **Targeting** is a probability density over every way a surviving ship could
  still lie, which is the mechanical advantage the arena's class-2 mark refers
  to. The no-touching rule is a gift here: every cell around a sunk ship is
  known empty, and ruling those out sharpens the next density map.
"""

from __future__ import annotations

from .base import Brain, Context, register

SIZE = 10
FLEET = (4, 3, 3, 2, 2, 2, 1, 1, 1, 1)

UNKNOWN, MISS, HIT = 0, 1, 2


def _cells(r: int, c: int, length: int, horizontal: bool) -> list[tuple[int, int]]:
    if horizontal:
        return [(r, c + i) for i in range(length)]
    return [(r + i, c) for i in range(length)]


def _in_bounds(cells: list[tuple[int, int]], size: int = SIZE) -> bool:
    return all(0 <= r < size and 0 <= c < size for r, c in cells)


def _halo(cells: list[tuple[int, int]], size: int = SIZE) -> set[tuple[int, int]]:
    """The cells a ship forbids: itself plus everything touching it."""
    out: set[tuple[int, int]] = set()
    for r, c in cells:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    out.add((rr, cc))
    return out


def random_fleet(rng, size: int = SIZE, fleet: tuple[int, ...] = FLEET) -> list[dict]:
    """A uniformly random legal fleet, retried until it fits. Longest ships go
    down first — placing the 4 last is what makes a layout fail."""
    for _ in range(400):
        blocked: set[tuple[int, int]] = set()
        ships: list[dict] = []
        ok = True
        for length in sorted(fleet, reverse=True):
            options = []
            for horizontal in (True, False):
                span = size - length + 1
                for r in range(size if horizontal else span):
                    for c in range(span if horizontal else size):
                        cells = _cells(r, c, length, horizontal)
                        if not _in_bounds(cells, size):
                            continue
                        if any(cell in blocked for cell in cells):
                            continue
                        options.append((r, c, horizontal, cells))
            if not options:
                ok = False
                break
            r, c, horizontal, cells = rng.choice(options)
            blocked |= _halo(cells, size)
            ships.append({"r": r, "c": c, "len": length, "dir": "h" if horizontal else "v"})
        if ok and len(ships) == len(fleet):
            # Reject fleets huddled in one corner: a shooter who finds the
            # cluster finds the rest for free.
            centres = [(s["r"], s["c"]) for s in ships]
            spread_r = max(r for r, _ in centres) - min(r for r, _ in centres)
            spread_c = max(c for _, c in centres) - min(c for _, c in centres)
            if spread_r >= 5 and spread_c >= 5:
                return ships
    return ships


class Targeting:
    """What we know about the enemy board, and what to shoot next.

    Everything here is derived from `state.shotsMade`, which the arena repeats
    in full on every read. That matters twice over: it is correct after a
    restart or a gap in the mailbox, and it means no belief of ours can drift
    away from what the server actually says happened.

    A shot comes back as one of four results: `miss`, `hit`, `kill` (the shot
    that finished a ship) and `auto` — the cells the server marks for you
    around a wreck, which are *water*, since ships may not touch.
    """

    def __init__(self, size: int = SIZE, fleet: tuple[int, ...] = FLEET):
        self.size = size
        self.fleet = list(fleet)
        self.grid = [[UNKNOWN] * size for _ in range(size)]
        self.sunk_cells: set[tuple[int, int]] = set()
        self.sunk_lengths: list[int] = []

    def load(self, shots_made: list) -> None:
        kills: set[tuple[int, int]] = set()
        hits: set[tuple[int, int]] = set()
        for entry in shots_made or []:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            r, c = int(entry[0]), int(entry[1])
            if not (0 <= r < self.size and 0 <= c < self.size):
                continue
            result = str(entry[2]) if len(entry) > 2 else "miss"
            if result in ("miss", "auto"):
                # `auto` is the halo the server marked around a sunk ship: it
                # is water, and treating it as a hit is how a targeting loop
                # ends up chasing ships that are not there.
                self.grid[r][c] = MISS
            else:
                self.grid[r][c] = HIT
                hits.add((r, c))
                if result == "kill":
                    kills.add((r, c))

        # A ship is a connected run of hits; it is sunk when the run contains
        # the shot that killed it. Nothing else needs to be remembered.
        for group in self._groups(hits):
            if group & kills:
                self.sunk_cells |= group
                self.sunk_lengths.append(len(group))
                for cell in _halo(sorted(group), self.size):
                    if cell not in self.sunk_cells and self.grid[cell[0]][cell[1]] == UNKNOWN:
                        self.grid[cell[0]][cell[1]] = MISS

    @staticmethod
    def _groups(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
        remaining = set(cells)
        out: list[set[tuple[int, int]]] = []
        while remaining:
            start = remaining.pop()
            group = {start}
            stack = [start]
            while stack:
                r, c = stack.pop()
                for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    neighbour = (r + dr, c + dc)
                    if neighbour in remaining:
                        remaining.discard(neighbour)
                        group.add(neighbour)
                        stack.append(neighbour)
            out.append(group)
        return out

    @property
    def remaining(self) -> list[int]:
        left = list(self.fleet)
        for length in self.sunk_lengths:
            if length in left:
                left.remove(length)
        return left

    def density(self) -> list[list[int]]:
        """How many surviving-ship placements cover each cell. Placements that
        cover a known hit are weighted up: a ship that explains a hit we have
        is far likelier than one hiding in untouched water."""
        board = [[0] * self.size for _ in range(self.size)]
        for length in set(self.remaining):
            count = self.remaining.count(length)
            for horizontal in (True, False):
                if length == 1 and not horizontal:
                    continue
                for r in range(self.size):
                    for c in range(self.size):
                        cells = _cells(r, c, length, horizontal)
                        if not _in_bounds(cells, self.size):
                            continue
                        if any(self.grid[rr][cc] == MISS for rr, cc in cells):
                            continue
                        if any((rr, cc) in self.sunk_cells for rr, cc in cells):
                            continue
                        covered = sum(1 for rr, cc in cells if self.grid[rr][cc] == HIT)
                        weight = count * (1 + 12 * covered)
                        for rr, cc in cells:
                            if self.grid[rr][cc] == UNKNOWN:
                                board[rr][cc] += weight
        return board

    def next_shot(self, rng) -> tuple[int, int]:
        """One rule for both hunting and finishing.

        Weighting placements that explain a hit we already have makes the
        density map finish a wounded ship on its own, and it does it better
        than a hand-written "extend the line" rule — because it also knows
        which continuations are impossible given the ships still afloat.
        """
        board = self.density()
        best, cells = -1, []
        for r in range(self.size):
            for c in range(self.size):
                if self.grid[r][c] != UNKNOWN:
                    continue
                if board[r][c] > best:
                    best, cells = board[r][c], [(r, c)]
                elif board[r][c] == best:
                    cells.append((r, c))
        if not cells:
            free = [
                (r, c)
                for r in range(self.size)
                for c in range(self.size)
                if self.grid[r][c] == UNKNOWN
            ]
            return rng.choice(free) if free else (0, 0)
        return rng.choice(cells)


@register
class SeabattleBrain(Brain):
    game = "seabattle"

    def __init__(self) -> None:
        self.targeting: Targeting | None = None
        self.shots = 0
        self.hits = 0

    def on_event(self, event: dict, ctx: Context) -> None:
        if event.get("type") != "shot_result":
            return
        if ctx.seat is not None and str(event.get("by")) != str(ctx.seat):
            return
        self.shots += 1
        if event.get("result") in ("hit", "kill"):
            self.hits += 1

    def choose(self, state: dict, ctx: Context) -> dict | None:
        phase = state.get("phase")
        size = int(state.get("size") or SIZE)
        fleet = tuple(int(x) for x in (state.get("fleet") or FLEET))

        if phase == "placing":
            if state.get("placed"):
                return None
            return {"type": "place", "ships": random_fleet(ctx.rng, size, fleet)}

        if phase != "battle" or not self.my_turn(state):
            return None

        # Rebuilt from the server's own history every turn, so a restart or a
        # gap in the mailbox cannot leave us shooting at a stale picture.
        self.targeting = Targeting(size, fleet)
        self.targeting.load(state.get("shotsMade") or [])

        r, c = self.targeting.next_shot(ctx.rng)
        return {"type": "shot", "r": r, "c": c}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        if not self.shots:
            return None
        rate = 100.0 * self.hits / self.shots
        return (
            f"Good game. I place at random (no pattern to read) and shoot a probability density over every way a "
            f"surviving ship could still lie, using the no-touching rule to rule out the ring around each wreck. "
            f"That came to {self.hits}/{self.shots} shots on target — {rate:.0f}%. What was your targeting?"
        )

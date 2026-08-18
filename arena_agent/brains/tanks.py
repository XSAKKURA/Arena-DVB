"""Blind Tanks — battleship where the enemy is invisible *and* moves.

The whole game is an inference problem, and the arena is generous with
evidence whether the players like it or not: you must move every turn, and
every turn you give away the cell you left — as dust if you were quiet, as the
`from` of your shot if you were not. So the enemy's position is never unknown
for long, only blurred by one step of movement per turn.

We keep a probability grid, reset it hard on every sighting, and blur it by one
move each turn. Shooting is free (it reveals exactly what dust would have
revealed anyway), so we shoot every single turn.
"""

from __future__ import annotations

from .base import Brain, Context, register

STEPS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]


class Belief:
    """Where the enemy probably is, given everything we have been told."""

    def __init__(self, n: int):
        self.n = n
        self.grid = [[1.0] * n for _ in range(n)]
        self.normalise()

    def playable(self, x: int, y: int, water: int) -> bool:
        return water <= x < self.n - water and water <= y < self.n - water

    def normalise(self) -> None:
        total = sum(sum(row) for row in self.grid)
        if total <= 0:
            self.grid = [[1.0] * self.n for _ in range(self.n)]
            total = self.n * self.n
        self.grid = [[value / total for value in row] for row in self.grid]

    def drop_water(self, water: int) -> None:
        for x in range(self.n):
            for y in range(self.n):
                if not self.playable(x, y, water):
                    self.grid[x][y] = 0.0
        self.normalise()

    def fix_at(self, x: int, y: int) -> None:
        """We know exactly where they are (they are adjacent to us)."""
        self.grid = [[0.0] * self.n for _ in range(self.n)]
        self.grid[x][y] = 1.0

    def fix_around(self, x: int, y: int, water: int) -> None:
        """They *left* this cell, so they are in one of the eight around it."""
        self.grid = [[0.0] * self.n for _ in range(self.n)]
        for dx, dy in STEPS:
            xx, yy = x + dx, y + dy
            if 0 <= xx < self.n and 0 <= yy < self.n and self.playable(xx, yy, water):
                self.grid[xx][yy] = 1.0
        self.normalise()

    def blur_one_move(self, water: int) -> None:
        """A turn passed and they had to move exactly one step."""
        fresh = [[0.0] * self.n for _ in range(self.n)]
        for x in range(self.n):
            for y in range(self.n):
                mass = self.grid[x][y]
                if mass <= 0:
                    continue
                targets = [
                    (x + dx, y + dy)
                    for dx, dy in STEPS
                    if 0 <= x + dx < self.n
                    and 0 <= y + dy < self.n
                    and self.playable(x + dx, y + dy, water)
                ]
                if not targets:
                    fresh[x][y] += mass
                    continue
                share = mass / len(targets)
                for xx, yy in targets:
                    fresh[xx][yy] += share
        self.grid = fresh
        self.normalise()

    def rule_out(self, x: int, y: int, weight: float = 0.0) -> None:
        if 0 <= x < self.n and 0 <= y < self.n:
            self.grid[x][y] *= weight
            self.normalise()

    def best_cell(self, water: int, avoid: tuple[int, int] | None = None) -> tuple[int, int]:
        best, cells = -1.0, []
        for x in range(self.n):
            for y in range(self.n):
                if not self.playable(x, y, water) or (x, y) == avoid:
                    continue
                value = self.grid[x][y]
                if value > best:
                    best, cells = value, [(x, y)]
                elif value == best:
                    cells.append((x, y))
        return cells[0] if cells else (water, water)

    def mass_near(self, x: int, y: int, radius: int = 1) -> float:
        total = 0.0
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.n and 0 <= yy < self.n:
                    total += self.grid[xx][yy]
        return total


@register
class TanksBrain(Brain):
    game = "tanks"

    def __init__(self) -> None:
        self.belief: Belief | None = None
        self.last_turn_seen = -1
        self.shots = 0
        self.hits = 0
        self.pending_shot: tuple[int, int] | None = None

    def _ensure(self, state: dict) -> Belief:
        n = int(state.get("n") or 7)
        if self.belief is None or self.belief.n != n:
            self.belief = Belief(n)
        return self.belief

    def on_event(self, event: dict, ctx: Context) -> None:
        kind = event.get("type")
        belief = self.belief
        if belief is None:
            return

        if kind == "enemy_shot":
            # They shot from `from` and then moved: eight cells around it.
            origin = event.get("from") or {}
            if "x" in origin and "y" in origin:
                belief.fix_around(int(origin["x"]), int(origin["y"]), 0)
        elif kind == "shot_result":
            self.shots += 1
            if event.get("result") == "hit" or event.get("hit"):
                self.hits += 1
                if self.pending_shot:
                    # A hit pins them exactly — then they move one step.
                    belief.fix_around(*self.pending_shot, 0)
            elif self.pending_shot:
                belief.rule_out(*self.pending_shot)
            self.pending_shot = None
        elif kind == "hit_taken":
            origin = event.get("from") or {}
            if "x" in origin and "y" in origin:
                belief.fix_around(int(origin["x"]), int(origin["y"]), 0)
        elif kind in ("moved", "turn"):
            if ctx.seat is not None and str(event.get("by")) == str(ctx.seat):
                return
            dust = event.get("dust")
            if isinstance(dust, dict) and "x" in dust:
                belief.fix_around(int(dust["x"]), int(dust["y"]), 0)
            else:
                belief.blur_one_move(0)

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        belief = self._ensure(state)
        n = belief.n
        water = int(state.get("water") or 0)
        me = state.get("me") or {}
        my_x, my_y = int(me.get("x", 0)), int(me.get("y", 0))

        enemy = state.get("enemy")
        if isinstance(enemy, dict) and "x" in enemy:
            belief.fix_at(int(enemy["x"]), int(enemy["y"]))
        dust = (state.get("dust") or {}).get("foe")
        if isinstance(dust, dict) and "x" in dust:
            belief.fix_around(int(dust["x"]), int(dust["y"]), water)
        belief.drop_water(water)

        # Shoot first — it costs nothing, because moving quietly would have
        # given away the same cell as dust.
        if not state.get("shotThisTurn"):
            x, y = belief.best_cell(water, avoid=(my_x, my_y))
            self.pending_shot = (x, y)
            return {"type": "shoot", "x": x, "y": y}

        return self._move(state, belief, my_x, my_y, water, n, ctx)

    def _move(self, state, belief, my_x, my_y, water, n, ctx) -> dict:
        flood_in = int(state.get("floodIn") or 99)
        options = []
        for dx, dy in STEPS:
            xx, yy = my_x + dx, my_y + dy
            if not (0 <= xx < n and 0 <= yy < n):
                continue
            if not belief.playable(xx, yy, water):
                continue
            enemy = state.get("enemy")
            if isinstance(enemy, dict) and (int(enemy.get("x", -1)), int(enemy.get("y", -1))) == (xx, yy):
                continue
            # Being adjacent to them means being seen; being far means safe.
            exposure = belief.mass_near(xx, yy, radius=1)
            centre = (n - 1) / 2.0
            drift = abs(xx - centre) + abs(yy - centre)
            # The outer ring is about to become water — get off it in time.
            urgency = 2.0 if flood_in <= 2 else 0.35
            score = -exposure * 3.0 - drift * urgency + ctx.rng.random() * 0.25
            options.append((score, dx, dy))

        if not options:
            # Cornered: any legal step beats forfeiting the turn.
            for dx, dy in STEPS:
                xx, yy = my_x + dx, my_y + dy
                if 0 <= xx < n and 0 <= yy < n:
                    return {"type": "move", "dx": dx, "dy": dy}
            return {"type": "move", "dx": 1, "dy": 0}

        options.sort(reverse=True)
        _, dx, dy = options[0]
        return {"type": "move", "dx": dx, "dy": dy}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        if not self.shots:
            return None
        rate = 100.0 * self.hits / self.shots
        return (
            "Good game. I keep a probability grid for your tank: every sighting — dust, the cell you shot from, "
            "a hit — resets it to the eight cells around that square, and it blurs by one step each turn. "
            f"I shoot every turn because shooting gives away nothing dust would not. {self.hits}/{self.shots} on "
            f"target, {rate:.0f}%."
        )

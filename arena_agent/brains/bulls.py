"""Bulls and Cows.

The arena calls this class 2 because a solver can hold every consistent
candidate in mind, and that is exactly what this does: 5040 four-distinct-digit
numbers, filtered by every answer received, then a guess chosen to split what
remains as evenly as possible rather than to be lucky.

Picking the guess that minimises the *largest* surviving bucket (Knuth's
minimax, with expected size as the tie-break) solves a typical secret in about
five guesses, which is near the theoretical floor.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import permutations

from .base import Brain, Context, register


@lru_cache(maxsize=1)
def all_candidates() -> tuple[str, ...]:
    return tuple("".join(p) for p in permutations("0123456789", 4))


def feedback(guess: str, secret: str) -> tuple[int, int]:
    bulls = sum(1 for a, b in zip(guess, secret) if a == b)
    common = len(set(guess) & set(secret))
    return bulls, common - bulls


@register
class BullsBrain(Brain):
    game = "bulls"

    def __init__(self) -> None:
        self.candidates: list[str] = list(all_candidates())
        self.applied = 0
        self.secret: str | None = None

    def _refilter(self, guesses: list[dict]) -> None:
        """Fold in every answer we have not folded in yet. The arena repeats
        the whole history, so this stays correct after a restart."""
        if len(guesses) <= self.applied:
            return
        for record in guesses[self.applied :]:
            guess = str(record.get("guess") or "")
            if len(guess) != 4:
                continue
            bulls = int(record.get("bulls") or 0)
            cows = int(record.get("cows") or 0)
            self.candidates = [
                c for c in self.candidates if feedback(guess, c) == (bulls, cows)
            ]
        self.applied = len(guesses)

    def _next_guess(self, ctx: Context) -> str:
        if not self.candidates:
            # Should not happen; if the answers were inconsistent, start over
            # rather than freeze.
            self.candidates = list(all_candidates())
            self.applied = 0
        if len(self.candidates) == 1:
            return self.candidates[0]
        if len(self.candidates) > 1200:
            # Nothing informative to compute yet: two disjoint openings pin
            # down which digits are even in play faster than anything clever.
            return "0123" if self.applied == 0 else "4567"

        pool = self.candidates
        if len(pool) > 320:
            pool = ctx.rng.sample(pool, 320)

        best_guess, best_key = pool[0], (10**9, 10**9)
        for guess in pool:
            buckets: dict[tuple[int, int], int] = {}
            for secret in self.candidates:
                key = feedback(guess, secret)
                buckets[key] = buckets.get(key, 0) + 1
                if buckets[key] > best_key[0]:
                    break  # cannot beat the incumbent; abandon early
            else:
                worst = max(buckets.values())
                expected = sum(n * n for n in buckets.values())
                if (worst, expected) < best_key:
                    best_key = (worst, expected)
                    best_guess = guess
        return best_guess

    def choose(self, state: dict, ctx: Context) -> dict | None:
        phase = state.get("phase")

        if phase == "setup":
            if state.get("secretSet"):
                return None
            # A uniformly random secret is unexploitable; there is nothing
            # cleverer to do on this side of the board.
            self.secret = "".join(ctx.rng.sample("0123456789", 4))
            return {"type": "set_secret", "number": self.secret}

        if phase != "play" or not self.my_turn(state):
            return None

        self._refilter(list(state.get("myGuesses") or []))
        return {"type": "guess", "number": self._next_guess(ctx)}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        used = len(state.get("myGuesses") or [])
        return (
            f"Good game. I keep all 5040 four-distinct-digit numbers and drop every one inconsistent with your "
            f"answers, then guess whatever splits the survivors most evenly (Knuth minimax). "
            f"That took {used} guesses; {len(self.candidates)} candidates were still standing at the end."
        )

"""Games decided by modelling the other side rather than by search.

Rock-paper-scissors and its five-gesture cousin, karateka, three fronts
(Colonel Blotto) and the pact (iterated prisoner's dilemma with public
promises). None of them has a position to evaluate; all of them are won by
predicting the opponent one step better than they predict you.
"""

from __future__ import annotations

import math
from collections import Counter

from .base import Brain, Context, register

# ---------------------------------------------------------------------------
# Rock-paper-scissors, and RPS-Lizard-Spock
# ---------------------------------------------------------------------------

RPS_BEATS = {"r": {"s"}, "p": {"r"}, "s": {"p"}}
RPSLS_BEATS = {
    "r": {"s", "l"},
    "p": {"r", "v"},
    "s": {"p", "l"},
    "l": {"p", "v"},
    "v": {"r", "s"},
}


class _ThrowBrain(Brain):
    """Uniform random is the Nash equilibrium and cannot be exploited, so it is
    the floor we never go below. Against an opponent who visibly is *not*
    random we tilt towards the counter — deviating from Nash costs nothing in
    expectation against a Nash player, so a detected bias is free money."""

    moves: list[str] = []
    beats: dict[str, set[str]] = {}
    # How many observations before we trust a bias enough to act on it.
    min_samples = 9

    def __init__(self) -> None:
        self.seen: Counter[str] = Counter()

    def on_event(self, event: dict, ctx: Context) -> None:
        if event.get("type") == "game_result":
            theirs = event.get("theirs")
            if theirs in self.moves:
                self.seen[theirs] += 1
                if ctx.store:
                    ctx.store.update_opponent(ctx.opponent_name, self.game, {theirs: 1})

    def _distribution(self, ctx: Context) -> dict[str, float]:
        counts = Counter(self.seen)
        if ctx.store:
            for move, n in ctx.store.opponent_history(ctx.opponent_name, self.game).items():
                if move in self.moves and isinstance(n, (int, float)):
                    counts[move] += n
        total = sum(counts.values())
        if total < self.min_samples:
            return {m: 1 / len(self.moves) for m in self.moves}
        # Laplace smoothing keeps a single observation from looking like a law.
        smoothing = 2.0
        denominator = total + smoothing * len(self.moves)
        return {m: (counts.get(m, 0) + smoothing) / denominator for m in self.moves}

    def _pick(self, ctx: Context) -> str:
        distribution = self._distribution(ctx)
        spread = max(distribution.values()) - min(distribution.values())
        if spread < 0.08:
            return ctx.rng.choice(self.moves)

        scores = {}
        for mine in self.moves:
            scores[mine] = sum(
                probability * (1 if theirs in self.beats[mine] else (-1 if mine in self.beats[theirs] else 0))
                for theirs, probability in distribution.items()
            )
        best = max(scores.values())
        top = [m for m, s in scores.items() if s >= best - 1e-9]
        # Keep a third of our throws honest-random so that exploiting a biased
        # opponent does not hand a counter-exploiter a pattern of our own.
        if ctx.rng.random() < 0.30:
            return ctx.rng.choice(self.moves)
        return ctx.rng.choice(top)

    def choose(self, state: dict, ctx: Context) -> dict | None:
        mine = state.get("myMatch")
        if not mine or mine.get("thrown"):
            return None
        return {"type": "throw", "v": self._pick(ctx)}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        total = sum(self.seen.values())
        if total < 3:
            return "Good game. I threw uniformly at random — that is the Nash equilibrium here, so there was nothing to read."
        top, n = self.seen.most_common(1)[0]
        return (
            f"Good game. I default to a uniform random throw (Nash) and only deviate when I see a bias; "
            f"across our throws your most frequent was '{top}' ({n}/{total}). What were you doing?"
        )


@register
class RpsBrain(_ThrowBrain):
    game = "rps"
    moves = ["r", "p", "s"]
    beats = RPS_BEATS


@register
class RpslsBrain(_ThrowBrain):
    game = "rpsls"
    moves = ["r", "p", "s", "l", "v"]
    beats = RPSLS_BEATS


# ---------------------------------------------------------------------------
# Karateka
# ---------------------------------------------------------------------------

KARATE_MOVES = ["strike", "grab", "block"]
# key beats value
KARATE_BEATS = {"strike": "grab", "grab": "block", "block": "strike"}
# the move that beats the key
KARATE_COUNTER = {"strike": "block", "grab": "strike", "block": "grab"}


@register
class KaratekaBrain(Brain):
    """The station bot counters our *most frequent* move 55% of the time, so
    the exploit is not "find its bias" — it is to keep an exact copy of the
    frequency table it is keeping on us, predict what it will counter, and play
    the move that beats that. Doing so rotates our own favourite, which rotates
    its counter, and we stay one step ahead of it all match.

    Against another agent there is no such handle, so we fall back to a
    recency-weighted read of what they have actually thrown."""

    game = "karateka"

    def __init__(self) -> None:
        self.my_moves: list[str] = []
        self.their_moves: list[str] = []
        self.pending: str | None = None
        self.round_seen = -1

    def on_event(self, event: dict, ctx: Context) -> None:
        if event.get("type") != "clash":
            return
        acts = event.get("acts") or {}
        for seat, act in acts.items():
            if act not in KARATE_MOVES:
                continue
            if ctx.seat is not None and str(seat) == str(ctx.seat):
                self.my_moves.append(act)
            else:
                self.their_moves.append(act)
                if ctx.store:
                    ctx.store.update_opponent(ctx.opponent_name, self.game, {act: 1})
        self.pending = None

    def _robik_prediction(self) -> dict[str, float]:
        """Robik's own distribution, reconstructed from the table it keeps on
        us: counter of our argmax at 0.55, uniform for the rest."""
        distribution = {m: 0.45 / 3 for m in KARATE_MOVES}
        if not self.my_moves:
            return distribution
        counts = Counter(self.my_moves)
        best = max(counts.values())
        favourites = [m for m in KARATE_MOVES if counts.get(m, 0) == best]
        share = 0.55 / len(favourites)
        for favourite in favourites:
            distribution[KARATE_COUNTER[favourite]] += share
        return distribution

    def _agent_prediction(self, ctx: Context) -> dict[str, float]:
        counts = {m: 1.0 for m in KARATE_MOVES}  # uniform prior
        for index, move in enumerate(self.their_moves):
            # Recent rounds say more about what they will do next.
            counts[move] += math.exp((index - len(self.their_moves)) / 4.0) * 4.0
        if ctx.store:
            for move, n in ctx.store.opponent_history(ctx.opponent_name, self.game).items():
                if move in counts and isinstance(n, (int, float)):
                    counts[move] += min(4.0, n * 0.25)
        total = sum(counts.values())
        return {m: c / total for m, c in counts.items()}

    def _pick(self, ctx: Context) -> str:
        prediction = self._robik_prediction() if ctx.vs_bot else self._agent_prediction(ctx)
        scores = {}
        for mine in KARATE_MOVES:
            scores[mine] = sum(
                probability
                * (1 if KARATE_BEATS[mine] == theirs else (-1 if KARATE_BEATS[theirs] == mine else 0))
                for theirs, probability in prediction.items()
            )
        best = max(scores.values())
        top = [m for m, s in scores.items() if s >= best - 1e-9]
        if not ctx.vs_bot and ctx.rng.random() < 0.20:
            return ctx.rng.choice(KARATE_MOVES)
        return ctx.rng.choice(top)

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if state.get("phase") != "pick" or state.get("picked"):
            return None
        current = state.get("round")
        if self.pending is not None and current == self.round_seen:
            return None  # already sent this round, waiting for the reveal
        move = self._pick(ctx)
        self.pending = move
        self.round_seen = current
        return {"type": "act", "a": move}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        if ctx.vs_bot:
            return (
                "Good game. I keep a copy of the frequency table Robik keeps on me, predict which move it will "
                "counter, and play what beats that — so my own favourite rotates on purpose."
            )
        if not self.their_moves:
            return None
        top, n = Counter(self.their_moves).most_common(1)[0]
        return (
            f"Good game. I was reading you with a recency-weighted frequency model — '{top}' came up {n} times "
            f"out of {len(self.their_moves)}. Were you randomising or countering me?"
        )


# ---------------------------------------------------------------------------
# Three Fronts — Colonel Blotto
# ---------------------------------------------------------------------------


def _blotto_allocations(budget: int = 13) -> list[tuple[int, int, int]]:
    return [
        (a, b, budget - a - b)
        for a in range(budget + 1)
        for b in range(budget - a + 1)
    ]


@register
class ThreeFrontsBrain(Brain):
    """No allocation dominates, so the whole game is the distribution you draw
    from. We keep a belief over what the opponent plays — a broad prior plus
    everything we have actually seen them do — and answer it with a softmax
    over best responses, which stays mixed instead of becoming a pattern the
    opponent can counter in round three."""

    game = "threefronts"
    gates = (1, 2, 3)

    def __init__(self) -> None:
        self.allocations = _blotto_allocations(13)
        self.seen: list[tuple[int, int, int]] = []
        self.sent_round = -1

    def on_event(self, event: dict, ctx: Context) -> None:
        if event.get("type") != "resolved":
            return
        splits = event.get("splits") or {}
        for seat, split in splits.items():
            if ctx.seat is not None and str(seat) == str(ctx.seat):
                continue
            if isinstance(split, list) and len(split) == 3:
                self.seen.append(tuple(int(x) for x in split))

    def _score(self, mine: tuple[int, int, int], theirs: tuple[int, int, int]) -> int:
        total = 0
        for gate, (a, b) in enumerate(zip(mine, theirs)):
            if a > b:
                total += self.gates[gate]
            elif b > a:
                total -= self.gates[gate]
        return total

    def _belief(self, ctx: Context) -> list[tuple[tuple[int, int, int], float]]:
        weights: dict[tuple[int, int, int], float] = {}
        # A flat prior over every legal split: it is wrong, but it is wrong in
        # no particular direction, which is what a prior is for.
        for allocation in self.allocations:
            weights[allocation] = 1.0
        # Observed rounds count for much more, and the most recent most of all.
        for index, split in enumerate(self.seen):
            weight = 40.0 * math.exp((index - len(self.seen) + 1) / 2.0)
            weights[split] = weights.get(split, 0.0) + weight
            # Opponents repeat *shapes* more than exact numbers, so smear a
            # little probability onto the neighbours of what we saw.
            for neighbour in self.allocations:
                distance = sum(abs(x - y) for x, y in zip(neighbour, split))
                if 0 < distance <= 4:
                    weights[neighbour] = weights.get(neighbour, 0.0) + weight * 0.25 / distance
        total = sum(weights.values())
        return [(k, v / total) for k, v in weights.items()]

    def _pick(self, ctx: Context) -> tuple[int, int, int]:
        belief = self._belief(ctx)
        scores = []
        for mine in self.allocations:
            expected = sum(probability * self._score(mine, theirs) for theirs, probability in belief)
            scores.append((expected, mine))
        scores.sort(reverse=True)
        # Softmax over the top of the list: still a best response, but a mixed
        # one, so five rounds do not become five readable rounds.
        top = scores[: max(4, len(scores) // 12)]
        best = top[0][0]
        weights = [math.exp((s - best) * 2.5) for s, _ in top]
        return ctx.rng.choices([a for _, a in top], weights=weights, k=1)[0]

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if state.get("submitted"):
            return None
        current = state.get("round")
        if current == self.sent_round:
            return None
        allocation = self._pick(ctx)
        self.sent_round = current
        return {"type": "split", "a": allocation[0], "b": allocation[1], "c": allocation[2]}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. I hold a belief over your splits — a flat prior plus what you actually played, smeared "
            "onto nearby shapes — and answer with a softmax over best responses so I stay mixed. "
            f"You showed me {len(self.seen)} splits; what were you drawing from?"
        )


# ---------------------------------------------------------------------------
# The Pact — iterated prisoner's dilemma with public promises
# ---------------------------------------------------------------------------


@register
class PactBrain(Brain):
    """Cooperate honestly, retaliate once for every defection, forgive, and
    take the last round — mutual cooperation is a draw here, so the only way
    the match is *won* is by defecting when they do not, and the final round is
    the one where that costs nothing. Rounds 1..n-1 are played straight, which
    is also what keeps the public "promises kept" count high."""

    game = "pact"

    def __init__(self) -> None:
        self.retaliating = False
        self.sent_promise_round = -1
        self.sent_move_round = -1

    def _opponent_seat(self, state: dict, ctx: Context) -> str | None:
        for seat in (state.get("totals") or {}):
            if ctx.seat is None or str(seat) != str(ctx.seat):
                return str(seat)
        return None

    def _their_last_move(self, state: dict, ctx: Context) -> str | None:
        history = state.get("history") or []
        if not history:
            return None
        moves = history[-1].get("moves") or {}
        opponent = self._opponent_seat(state, ctx)
        if opponent and opponent in moves:
            return moves[opponent]
        for seat, move in moves.items():
            if ctx.seat is None or str(seat) != str(ctx.seat):
                return move
        return None

    def _intended_move(self, state: dict, ctx: Context) -> str:
        round_no = int(state.get("round") or 1)
        rounds = int(state.get("rounds") or 8)

        # The last round: nothing they do afterwards can punish it, and a match
        # of mutual cooperation is a draw, not a win.
        if round_no >= rounds:
            return "d"

        their_last = self._their_last_move(state, ctx)
        if their_last == "d":
            self.retaliating = True
            return "d"
        if self.retaliating:
            # One answer, then back to cooperating: grudges lose points.
            self.retaliating = False
            return "c"

        # They announced a betrayal — believe them.
        promises = state.get("roundPromises") or {}
        opponent = self._opponent_seat(state, ctx)
        if opponent and promises.get(opponent) == "betray":
            return "d"
        return "c"

    def choose(self, state: dict, ctx: Context) -> dict | None:
        phase = state.get("phase")
        round_no = state.get("round")

        if phase == "promise":
            if state.get("promised") or round_no == self.sent_promise_round:
                return None
            self.sent_promise_round = round_no
            rounds = int(state.get("rounds") or 8)
            allowed = state.get("promises") or ["cooperate", "betray", "alternate"]
            # Retaliation is announced out loud: it reads as a rule rather than
            # as spite, and it invites the opponent back to cooperating.
            if self.retaliating and "betray" in allowed:
                return {"type": "promise", "p": "betray"}
            if int(round_no or 1) >= rounds and "cooperate" in allowed:
                return {"type": "promise", "p": "cooperate"}
            return {"type": "promise", "p": "cooperate" if "cooperate" in allowed else allowed[0]}

        if phase == "move":
            if state.get("moved") or round_no == self.sent_move_round:
                return None
            self.sent_move_round = round_no
            return {"type": "move", "m": self._intended_move(state, ctx)}

        return None

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        kept = (state.get("keptCount") or {}).get(str(ctx.seat), "?")
        return (
            "Good game. My rule was: cooperate, answer a defection exactly once and say so in the promise, "
            f"forgive, and defect on the final round because mutual cooperation only draws. I kept {kept} of my "
            "promises. What were you running?"
        )

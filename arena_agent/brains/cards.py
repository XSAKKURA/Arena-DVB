"""The card games: Durak, President and Cheat.

All three share a deck encoding — a card is an integer, rank index is `id % 9`
(0..4 are 6..10, then jack, queen, king, ace) and suit is `id // 9` — and all
three are games of imperfect information where the right play is usually the
cheapest one that does the job. The heuristics below are written around that:
spend the smallest card that wins, keep the expensive ones for when they decide
something, and count what the opponent cannot possibly be holding.
"""

from __future__ import annotations

from collections import Counter

from .base import Brain, Context, register

RANK_NAMES = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def rank_of(card: int) -> int:
    return card % 9


def suit_of(card: int) -> int:
    return card // 9


# ---------------------------------------------------------------------------
# Durak
# ---------------------------------------------------------------------------


@register
class DurakBrain(Brain):
    game = "durak"

    def _beats(self, attacker: int, defender: int, trump_suit: int) -> bool:
        if suit_of(defender) == suit_of(attacker):
            return rank_of(defender) > rank_of(attacker)
        return suit_of(defender) == trump_suit

    def _value(self, card: int, trump_suit: int) -> int:
        """What spending this card costs us. Trumps are dear."""
        return rank_of(card) + (30 if suit_of(card) == trump_suit else 0)

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state) and not state.get("canAttack"):
            return None

        hand = list(state.get("hand") or [])
        if not hand:
            return None
        trump = state.get("trump") or {}
        trump_suit = int(trump.get("suit", -1))
        table = list(state.get("table") or [])
        role = state.get("role")
        deck_left = int(state.get("deckLeft") or 0)

        if role == "defender" and not state.get("taking"):
            return self._defend(state, hand, table, trump_suit, deck_left)

        if role == "attacker" and not table:
            return {"type": "attack", "card": self._cheapest_attack(hand, trump_suit)}

        if state.get("canAttack") and table:
            return self._throw_in(state, hand, table, trump_suit, deck_left)

        if self.my_turn(state) and not state.get("attackDone"):
            return {"type": "done"}
        return None

    def _defend(self, state, hand, table, trump_suit, deck_left) -> dict:
        unbeaten = [(i, pair["a"]) for i, pair in enumerate(table) if pair.get("d") is None]
        if not unbeaten:
            return {"type": "done"}

        # Can we cover everything, and is it worth what it costs?
        plan: list[tuple[int, int]] = []
        available = list(hand)
        total_cost = 0
        for index, attack in sorted(unbeaten, key=lambda item: rank_of(item[1])):
            options = [c for c in available if self._beats(attack, c, trump_suit)]
            if not options:
                plan = []
                break
            best = min(options, key=lambda c: self._value(c, trump_suit))
            available.remove(best)
            total_cost += self._value(best, trump_suit)
            plan.append((index, best))

        if not plan:
            return {"type": "take"}

        # Burning several trumps early, while the deck can still refill the
        # attacker, is usually worse than picking the cards up.
        attack_value = sum(rank_of(pair["a"]) for _, pair in enumerate(table) if pair.get("d") is None)
        trumps_spent = sum(1 for _, card in plan if suit_of(card) == trump_suit)
        if deck_left > 4 and trumps_spent >= 2 and total_cost > attack_value + 45:
            return {"type": "take"}

        index, card = plan[0]
        return {"type": "defend", "idx": index, "card": card}

    def _cheapest_attack(self, hand: list[int], trump_suit: int) -> int:
        non_trump = [c for c in hand if suit_of(c) != trump_suit]
        pool = non_trump or hand
        return min(pool, key=lambda c: self._value(c, trump_suit))

    def _throw_in(self, state, hand, table, trump_suit, deck_left) -> dict:
        on_table = {rank_of(pair["a"]) for pair in table}
        on_table |= {rank_of(pair["d"]) for pair in table if pair.get("d") is not None}
        candidates = [c for c in hand if rank_of(c) in on_table]
        if not candidates:
            return {"type": "done"}

        defender_cards = 0
        counts = state.get("counts") or {}
        defender = str(state.get("defender"))
        if defender in counts:
            defender_cards = int(counts[defender])
        unbeaten = sum(1 for pair in table if pair.get("d") is None)

        if state.get("taking"):
            # They have already given up: pile on everything cheap.
            cheap = [c for c in candidates if suit_of(c) != trump_suit]
            if cheap:
                return {"type": "attack", "card": min(cheap, key=lambda c: rank_of(c))}
            return {"type": "done"}

        if unbeaten >= defender_cards:
            return {"type": "done"}

        cheap = [c for c in candidates if suit_of(c) != trump_suit and rank_of(c) <= 4]
        if cheap:
            return {"type": "attack", "card": min(cheap, key=lambda c: rank_of(c))}
        if deck_left == 0:
            non_trump = [c for c in candidates if suit_of(c) != trump_suit]
            if non_trump:
                return {"type": "attack", "card": min(non_trump, key=lambda c: rank_of(c))}
        return {"type": "done"}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. My rule was to defend with the cheapest card that beats each attack, take the cards "
            "rather than burn two trumps early while the deck can still refill you, and throw in only low "
            "non-trumps."
        )


# ---------------------------------------------------------------------------
# President
# ---------------------------------------------------------------------------


@register
class PresidentBrain(Brain):
    game = "president"

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        hand = list(state.get("hand") or [])
        if not hand:
            return None
        trick = state.get("trick")
        groups: dict[int, list[int]] = {}
        for card in hand:
            groups.setdefault(rank_of(card), []).append(card)

        if not trick:
            return {"type": "play", "cards": self._lead(groups, hand)}

        count = int(trick.get("count") or 1)
        power = int(trick.get("power") or -1)
        options = [
            (rank, cards)
            for rank, cards in groups.items()
            if rank > power and len(cards) >= count
        ]
        if not options:
            return {"type": "pass"}

        # Going out ends the race in our favour — take it over anything subtle.
        for rank, cards in sorted(options):
            if len(hand) == count and len(cards) >= count:
                return {"type": "play", "cards": cards[:count]}

        # Otherwise the cheapest answer, and prefer not to break a group we
        # could later play whole.
        def cost(item):
            rank, cards = item
            return (rank, 1 if len(cards) > count else 0)

        rank, cards = min(options, key=cost)
        return {"type": "play", "cards": cards[:count]}

    def _lead(self, groups: dict[int, list[int]], hand: list[int]) -> list[int]:
        """Lead the lowest rank, and lead all of it: this is a race to an empty
        hand, so shedding more cards for the same trick is simply better."""
        lowest = min(groups)
        cards = groups[lowest]
        if len(hand) <= 4 and len(cards) == len(hand):
            return cards
        return cards[: min(4, len(cards))]

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        places = state.get("places") or []
        return (
            f"Good game. I lead my lowest rank and lead all of it — this is a race to an empty hand — and answer "
            f"with the cheapest legal set that does not break up a group. Final order: {places}."
        )


# ---------------------------------------------------------------------------
# Cheat — "believe"
# ---------------------------------------------------------------------------


@register
class BelieveBrain(Brain):
    game = "believe"

    def __init__(self) -> None:
        # How many cards of each rank have been *claimed* since the pile was
        # last cleared. More than four of a rank is a proven lie.
        self.claimed: Counter[int] = Counter()

    def on_event(self, event: dict, ctx: Context) -> None:
        kind = event.get("type")
        if kind == "played":
            rank = event.get("rankIndex")
            if isinstance(rank, int):
                self.claimed[rank] += int(event.get("count") or 0)
        elif kind == "reveal":
            self.claimed.clear()

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        hand = list(state.get("hand") or [])
        pile_size = int(state.get("pileSize") or 0)
        last_batch = state.get("lastBatch")
        current_rank = state.get("currentRankIndex")
        claimant = state.get("claimant")

        can_doubt = bool(last_batch) and pile_size > 0 and (
            ctx.seat is None or str(last_batch.get("pid")) != str(ctx.seat)
        )

        if can_doubt and self._should_doubt(state, hand, last_batch, current_rank, claimant, ctx):
            return {"type": "doubt"}

        return self._play(hand, current_rank, ctx)

    def _should_doubt(self, state, hand, last_batch, current_rank, claimant, ctx) -> bool:
        # Somebody has played their last cards: if this claim stands they win,
        # so the only losing move is to let it stand.
        if claimant is not None and (ctx.seat is None or str(claimant) != str(ctx.seat)):
            return True

        if not isinstance(current_rank, int):
            return False

        # Four of each rank exist. Count what we hold plus what has been
        # claimed: past four, somebody is provably lying.
        mine = sum(1 for card in hand if rank_of(card) == current_rank)
        claimed = self.claimed.get(current_rank, 0)
        if mine + claimed > 4:
            return True

        # A big batch of a rank we already hold most of is very likely a bluff.
        count = int(last_batch.get("count") or 1)
        room = 4 - mine - (claimed - count)
        if count > max(0, room):
            return True

        # Otherwise doubt occasionally, and more readily when the pile is small
        # so that being wrong is cheap.
        if pile := int(state.get("pileSize") or 0):
            if pile <= 4 and count >= 2:
                return ctx.rng.random() < 0.35
        return ctx.rng.random() < 0.08

    def _play(self, hand: list[int], current_rank, ctx: Context) -> dict:
        if not hand:
            return {"type": "doubt"}

        by_rank: dict[int, list[int]] = {}
        for card in hand:
            by_rank.setdefault(rank_of(card), []).append(card)

        if not isinstance(current_rank, int):
            # We open the round: claim the rank we hold most of and be honest,
            # which sheds the most cards with no risk at all.
            rank = max(by_rank, key=lambda r: len(by_rank[r]))
            cards = by_rank[rank][:4]
            return {"type": "play", "cards": cards, "rank": rank}

        honest = by_rank.get(current_rank, [])
        if honest:
            return {"type": "play", "cards": honest[:4]}

        # We have to lie. Shed from the rank we hold fewest of: the pairs and
        # triples are what we want to keep for an honest play later.
        rank = min(by_rank, key=lambda r: (len(by_rank[r]), -r))
        return {"type": "play", "cards": by_rank[rank][:1]}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. I count claims against the four copies of each rank that exist, so a claim I can prove "
            "impossible gets doubted every time — and I always doubt a player claiming their last cards, since "
            "letting that stand loses outright. When I lie, I shed singletons and keep my groups."
        )

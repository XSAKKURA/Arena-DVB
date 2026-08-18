"""Карточные игры: дурак, президент и «верю не верю».

У всех трёх одна кодировка колоды — карта это целое число, индекс ранга это
`id % 9` (0..4 это 6..10, дальше валет, дама, король, туз), а масть это `id // 9`,
— и все три являются играми с неполной информацией, где правильный ход обычно
самый дешёвый из тех, что решают задачу. Эвристики ниже написаны вокруг этого:
тратить наименьшую карту, которая выигрывает, беречь дорогие до момента, когда
они что-то решают, и считать то, чего у соперника быть не может.
"""

from __future__ import annotations

from collections import Counter

from .base import Brain, Context, register

RANK_NAMES = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def card_id(card) -> int:
    """Карты приходят двумя способами: голым числовым id в своей руке и
    объектом `{id, suit, power, rank}`, когда они уже на столе. Строка `rank` —
    локализованная подпись, полагаться надо на числа."""
    if isinstance(card, dict):
        return int(card.get("id", -1))
    return int(card)


def rank_of(card) -> int:
    return card_id(card) % 9


def suit_of(card) -> int:
    return card_id(card) // 9


# ---------------------------------------------------------------------------
# Durak
# ---------------------------------------------------------------------------


@register
class DurakBrain(Brain):
    game = "durak"

    def _beats(self, attacker, defender, trump_suit: int) -> bool:
        if suit_of(defender) == suit_of(attacker):
            return rank_of(defender) > rank_of(attacker)
        return suit_of(defender) == trump_suit

    def _value(self, card, trump_suit: int) -> int:
        """Во что нам обходится трата этой карты. Козыри дороги."""
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

        # Сможем ли покрыть всё и стоит ли оно того?
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

        # Сжечь несколько козырей рано, пока колода ещё может пополнить
        # атакующего, обычно хуже, чем взять карты.
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
            # Он уже сдался: подкидываем всё дешёвое.
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
            "Good game. Most of durak is deciding what a card is worth later rather than now — when a trump "
            "is worth spending and when picking up is cheaper. How were you valuing yours?"
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

        # Выйти значит закончить гонку в нашу пользу — это важнее любых тонкостей.
        for rank, cards in sorted(options):
            if len(hand) == count and len(cards) >= count:
                return {"type": "play", "cards": cards[:count]}

        # Иначе самый дешёвый ответ, и лучше не разбивать группу, которую позже
        # можно сыграть целиком.
        def cost(item):
            rank, cards = item
            return (rank, 1 if len(cards) > count else 0)

        rank, cards = min(options, key=cost)
        return {"type": "play", "cards": cards[:count]}

    def _lead(self, groups: dict[int, list[int]], hand: list[int]) -> list[int]:
        """Заходить с младшего ранга и заходить всем сразу: это гонка к пустой
        руке, поэтому сбросить больше карт за ту же взятку просто выгоднее."""
        lowest = min(groups)
        cards = groups[lowest]
        if len(hand) <= 4 and len(cards) == len(hand):
            return cards
        return cards[: min(4, len(cards))]

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        places = state.get("places") or []
        return (
            f"Good game. It is a race to an empty hand, so the question on every trick is whether a card is "
            f"worth more spent now or kept for control. Final order: {places}."
        )


# ---------------------------------------------------------------------------
# Cheat — "believe"
# ---------------------------------------------------------------------------


@register
class BelieveBrain(Brain):
    game = "believe"

    def __init__(self) -> None:
        # Сколько карт каждого ранга было *заявлено* с момента, когда стопку
        # последний раз убрали. Больше четырёх одного ранга — доказанная ложь.
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
        # Кто-то выложил свои последние карты: если заявка устоит, он выиграл,
        # так что единственный проигрышный ход — дать ей устоять.
        if claimant is not None and (ctx.seat is None or str(claimant) != str(ctx.seat)):
            return True

        if not isinstance(current_rank, int):
            return False

        # Каждого ранга существует четыре. Считаем то, что держим сами, плюс
        # заявленное: сверх четырёх кто-то доказуемо врёт.
        mine = sum(1 for card in hand if rank_of(card) == current_rank)
        claimed = self.claimed.get(current_rank, 0)
        if mine + claimed > 4:
            return True

        # Большая пачка ранга, которого у нас и так почти всё, — почти наверняка блеф.
        count = int(last_batch.get("count") or 1)
        room = 4 - mine - (claimed - count)
        if count > max(0, room):
            return True

        # В остальном сомневаемся изредка и охотнее, когда стопка мала: тогда
        # ошибиться дёшево.
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
            # Мы открываем круг: заявляем ранг, которого у нас больше всего, и
            # говорим правду — так сбрасывается больше всего карт без риска.
            rank = max(by_rank, key=lambda r: len(by_rank[r]))
            cards = by_rank[rank][:4]
            return {"type": "play", "cards": cards, "rank": rank}

        honest = by_rank.get(current_rank, [])
        if honest:
            return {"type": "play", "cards": honest[:4]}

        # Приходится врать. Сбрасываем из ранга, которого у нас меньше всего:
        # пары и тройки хочется приберечь для честного хода позже.
        rank = min(by_rank, key=lambda r: (len(by_rank[r]), -r))
        return {"type": "play", "cards": by_rank[rank][:1]}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. Four of each rank exist, so some claims can be shown impossible and some cannot; "
            "the rest is judging which of the possible ones you meant. Where did I misread you?"
        )

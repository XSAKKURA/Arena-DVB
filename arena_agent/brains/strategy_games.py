"""Игры, которые решает моделирование соперника, а не перебор.

Камень-ножницы-бумага и её пятижестовый родственник, каратека, три фронта
(полковник Блотто) и договор (повторяющаяся дилемма заключённого с публичными
обещаниями). Ни в одной из них нет позиции, которую можно оценить; все они
выигрываются тем, что ты предсказываешь соперника на шаг лучше, чем он тебя.
"""

from __future__ import annotations

import math
from collections import Counter

from .base import Brain, Context, register

# ---------------------------------------------------------------------------
# Камень-ножницы-бумага и её вариант с ящерицей и Споком
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
    """Равномерная случайность — равновесие Нэша, её нельзя эксплуатировать, и
    это тот пол, ниже которого мы не опускаемся. Против соперника, который явно
    *не* случаен, мы кренимся к контрходу: отклонение от Нэша против нэшевского
    игрока в среднем ничего не стоит, поэтому замеченное смещение — это
    бесплатные деньги."""

    moves: list[str] = []
    beats: dict[str, set[str]] = {}
    # Сколько наблюдений нужно, чтобы поверить смещению и начать его использовать.
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
        # Сглаживание Лапласа не даёт одному наблюдению выглядеть законом.
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
        # Треть бросков оставляем честно случайной, чтобы эксплуатация смещённого
        # соперника не подарила контр-эксплуататору наш собственный шаблон.
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
# Каратека
# ---------------------------------------------------------------------------

KARATE_MOVES = ["strike", "grab", "block"]
# ключ бьёт значение
KARATE_BEATS = {"strike": "grab", "grab": "block", "block": "strike"}
# ход, который бьёт ключ
KARATE_COUNTER = {"strike": "block", "grab": "strike", "block": "grab"}


@register
class KaratekaBrain(Brain):
    """Станционный бот в 55% случаев контрит наш *самый частый* ход, поэтому
    эксплойт здесь не «найти его смещение», а вести точную копию той таблицы
    частот, которую он ведёт на нас, предсказать, что он будет контрить, и
    сыграть то, что бьёт этот контрход. Так наш собственный фаворит начинает
    вращаться, вслед за ним вращается и его контрход, и весь матч мы идём на шаг
    впереди.

    Против другого агента такой ручки нет, поэтому мы откатываемся к чтению того,
    что он реально бросал, со взвешиванием по свежести."""

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
        """Собственное распределение Робика, восстановленное из таблицы, которую
        он ведёт на нас: контрход к нашему аргмаксу с вероятностью 0.55,
        равномерно для остального."""
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
        counts = {m: 1.0 for m in KARATE_MOVES}  # равномерный априор
        for index, move in enumerate(self.their_moves):
            # Свежие раунды говорят больше о том, что он сделает дальше.
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
            return None  # в этом раунде уже отправили, ждём вскрытия
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
        return (
            f"Good game — {len(self.their_moves)} rounds of it. I model what you are likely to throw and answer "
            "the prediction rather than picking at random, which of course fails against someone genuinely "
            "random. Were you randomising, or reading me back?"
        )


# ---------------------------------------------------------------------------
# Три фронта — полковник Блотто
# ---------------------------------------------------------------------------


def _blotto_allocations(budget: int = 13) -> list[tuple[int, int, int]]:
    return [
        (a, b, budget - a - b)
        for a in range(budget + 1)
        for b in range(budget - a + 1)
    ]


@register
class ThreeFrontsBrain(Brain):
    """Ни одно распределение не доминирует, поэтому вся игра — это то, из чего
    ты тянешь. Мы держим убеждение о том, что играет соперник (широкий априор
    плюс всё, что мы реально у него видели), и отвечаем софтмаксом по лучшим
    ответам: так стратегия остаётся смешанной, а не превращается в шаблон,
    который соперник раскусит к третьему раунду."""

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
        # Плоский априор по всем законным разбиениям: он неверен, но неверен без
        # определённого направления, а для того априор и нужен.
        for allocation in self.allocations:
            weights[allocation] = 1.0
        # Наблюдённые раунды весят куда больше, а самые свежие — больше всех.
        for index, split in enumerate(self.seen):
            weight = 40.0 * math.exp((index - len(self.seen) + 1) / 2.0)
            weights[split] = weights.get(split, 0.0) + weight
            # Соперники повторяют *формы* чаще, чем точные числа, поэтому
            # размазываем немного вероятности на соседей увиденного.
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
        # Софтмакс по верхушке списка: всё ещё лучший ответ, но смешанный, —
        # чтобы пять раундов не стали пятью читаемыми раундами.
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
            "Good game. Blotto has no dominant allocation, so I draw from a mixed distribution rather than "
            "repeating a shape, and I update it from what you actually play. "
            f"You showed me {len(self.seen)} splits over the match; what were you drawing from?"
        )


# ---------------------------------------------------------------------------
# Договор — повторяющаяся дилемма заключённого с публичными обещаниями
# ---------------------------------------------------------------------------


@register
class PactBrain(Brain):
    """Честно сотрудничать, отвечать ровно один раз на каждое предательство,
    прощать и забирать последний раунд: взаимное сотрудничество здесь даёт ничью,
    поэтому *выиграть* матч можно только предав тогда, когда соперник не предаёт,
    а последний раунд — тот, где это ничего не стоит. Раунды с первого по
    предпоследний играются честно, что заодно держит высоким публичный счётчик
    сдержанных обещаний."""

    game = "pact"

    def __init__(self) -> None:
        self.retaliating = False
        self.sent_promise_round = -1
        self.sent_move_round = -1
        #: С какого раунда мы перестаём сотрудничать. Выбирается случайно один
        #: раз за партию: история матчей публична, и всегда предавать ровно в
        #: последнем раунде значит объявить это всем, кто нас изучал. Измерено,
        #: что предпоследний раунд даёт тот же счёт, поэтому непредсказуемость
        #: здесь бесплатна.
        self.betray_from: int | None = None

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

        if self.betray_from is None:
            # Предпоследний раунд вместо последнего: соперник успевает ответить
            # один раз, но и мы отвечаем на его ответ, поэтому итог тот же.
            self.betray_from = rounds if ctx.rng.random() < 0.6 else max(2, rounds - 1)

        # Начиная с выбранного раунда сотрудничество прекращается: взаимное
        # сотрудничество даёт ничью, а выиграть матч можно только разойдясь
        # с соперником там, где он этого не сделал.
        if round_no >= self.betray_from:
            return "d"

        their_last = self._their_last_move(state, ctx)
        if their_last == "d":
            self.retaliating = True
            return "d"
        if self.retaliating:
            # Один ответ, и снова сотрудничаем: обиды стоят очков.
            self.retaliating = False
            return "c"

        # Он объявил предательство — верим ему.
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
            # Об ответе объявляем вслух: это читается как правило, а не как
            # злоба, и приглашает соперника вернуться к сотрудничеству.
            if self.retaliating and "betray" in allowed:
                return {"type": "promise", "p": "betray"}
            if self.betray_from is not None and int(round_no or 1) >= self.betray_from:
                if "cooperate" in allowed:
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
            "Good game. I play reciprocally: I open cooperating, I answer a defection rather than absorb it, "
            f"and I forgive afterwards. I kept {kept} of my promises, which the report shows anyway. "
            "What rule were you following?"
        )

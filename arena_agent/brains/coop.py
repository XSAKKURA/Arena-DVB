"""Две кооперативные игры: «Одна волна» и «Разум».

Обе не рейтинговые, и ни в одной нет соперника: за вторым местом сидит партнёр.
Это меняет смысл слов «играть хорошо»: в «Одной волне» задача — угадать, что
очевидно *обоим*, а в «Разуме» единственное доступное решение — когда действовать,
так что вся игра сводится к модели того, что держит партнёр.
"""

from __future__ import annotations

import time

from .base import Brain, Context, register

# Отсортировано по тому, насколько надёжно люди сходятся на них как на
# «очевидном». Сверяется со списком вариантов по порядку, так что побеждает
# самая сильная из присутствующих точек схождения.
FOCAL_WORDS = [
    "red", "circle", "dog", "apple", "blue", "cat", "square", "banana",
    "north", "fire", "water", "summer", "monday", "car", "sun", "moon",
    "green", "lion", "rose", "hammer", "guitar", "piano", "coffee", "gold",
    "left", "up", "yes", "one", "a", "heart", "star", "tree", "bird",
]


@register
class OneWaveBrain(Brain):
    game = "onewave"

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if state.get("picked"):
            return None
        options = [str(o) for o in (state.get("options") or [])]
        if not options:
            return None
        return {"type": "pick", "v": self._focal(options)}

    def _focal(self, options: list[str]) -> str:
        lowered = {option.lower().strip(): option for option in options}

        # У чисел свои, очень хорошо задокументированные точки схождения.
        numeric = {}
        for text, original in lowered.items():
            try:
                numeric[int(text)] = original
            except ValueError:
                pass
        if len(numeric) == len(options) and numeric:
            for preferred in (7, 3, 1):
                if preferred in numeric:
                    return numeric[preferred]
            return numeric[sorted(numeric)[len(numeric) // 2]]

        for word in FOCAL_WORDS:
            if word in lowered:
                return lowered[word]

        # Ничего семантически очевидного: откатываемся к единственному правилу,
        # которое оба видим и применяем одинаково, — первый вариант в общем списке.
        return options[0]

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        score = state.get("score")
        return (
            f"Good game — {score} matches. I go for the prototypical member of the category (red, circle, dog, "
            f"7 for a number), and when nothing stands out I take the first option, because the list order is "
            f"the one thing we both see identically. Worth comparing where we missed."
        )


@register
class MindBrain(Brain):
    game = "mind"

    def __init__(self) -> None:
        self.waiting_key: tuple | None = None
        self.waiting_since = 0.0

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if state.get("phase") != "play":
            self.waiting_key = None
            return None
        hand = sorted(int(c) for c in (state.get("hand") or []))
        if not hand:
            self.waiting_key = None
            return None

        lowest = hand[0]
        top = int(state.get("playedTop") or 0)
        counts = state.get("counts") or {}
        others = 0
        for seat, count in counts.items():
            if ctx.seat is None or str(seat) != str(ctx.seat):
                others += int(count)

        # Больше ни у кого ничего нет: вся наша рука ложится по порядку.
        if others <= 0:
            return {"type": "play"}

        gap = max(0, lowest - top - 1)
        if gap == 0:
            return {"type": "play"}

        # Сколько его карт ожидаемо лежит ниже нашей. Неизвестный запас — это всё,
        # что выше стопки и не находится в нашей руке.
        unknown = max(1, 100 - top - len(hand))
        expected_below = others * gap / unknown

        if expected_below < 0.12:
            return {"type": "play"}

        key = (state.get("level"), top, lowest)
        now = time.time()
        if key != self.waiting_key:
            self.waiting_key = key
            self.waiting_since = now
            return None

        # Ждём пропорционально тому, сколько вероятно лежит ниже нас: эта общая
        # договорённость и есть единственная «связь», которую разрешает игра.
        target = min(75.0, 7.0 * expected_below)
        if ctx.deadline_at:
            target = min(target, max(2.0, (ctx.deadline_at - now) * 0.5))
        if now - self.waiting_since >= target:
            return {"type": "play"}
        return None

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. I wait in proportion to how many of your cards I expect to be below my lowest — "
            "the expected count times about seven seconds — and play at once when nothing can be under me."
        )

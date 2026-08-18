"""«Правило» — индукция по числам от 1 до 100.

Список правил открыт: оба игрока видят все ~24, секретен только выбор. Это
превращает отгадывание в чистое отсеивание кандидатов, так что работа состоит из
(а) превращения текста каждого правила в предикат, который мы умеем вычислять, и
(б) пробы того числа, которое делит выживших кандидатов наиболее поровну.

Поскольку верная догадка даёт 11 минус число проб, а неверная — ноль, почти
всегда правильнее потратить ещё одну пробу, чем гадать между двумя кандидатами:
одно очко покупает уверенность ценой примерно в пять.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Callable

from .base import Brain, Context, register

log = logging.getLogger("arena.brain.rule")

DOMAIN = range(1, 101)


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    for d in range(2, int(math.isqrt(n)) + 1):
        if n % d == 0:
            return False
    return True


def _digit_sum(n: int) -> int:
    return sum(int(c) for c in str(n))


_TRIANGULAR = {k * (k + 1) // 2 for k in range(1, 20)}
_FIBONACCI = {1, 2, 3, 5, 8, 13, 21, 34, 55, 89}
_POWERS_OF_TWO = {2**k for k in range(0, 8)}
_SQUARES = {k * k for k in range(1, 11)}
_CUBES = {k**3 for k in range(1, 5)}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _EVEN(n: int) -> bool:
    return n % 2 == 0


def _ODD(n: int) -> bool:
    return n % 2 == 1


def _word_to_int(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def predicate_for(rule_id: str, text: str) -> Callable[[int], bool] | None:
    """Превратить правило в то, что мы умеем проверять. Возвращает None, если
    прочитать его не удалось: такое правило остаётся кандидатом, но вслепую его
    никогда не называют."""
    blob = f"{rule_id} {text}".lower()

    # Порядок важен: более специфичные шаблоны надо пробовать первыми.
    match = re.search(r"divisible by (\w+)|multiple of (\w+)|multiples of (\w+)", blob)
    if match:
        value = next((_word_to_int(g) for g in match.groups() if g), None)
        if value:
            return lambda n, k=value: n % k == 0

    # Формы, в которых арена пишет правила о сумме цифр. Проверено на реальном
    # тексте «the digits sum to an even number»: без формы «digits sum» разбор
    # проваливался в проверку слова «even» и возвращал предикат «число чётное»,
    # то есть другое правило.
    match = re.search(
        r"digits?\s+sum|sum\s+of\s+(?:its\s+|the\s+)?digits|digits?\s+add\s+up", blob
    )
    if match:
        inner = re.search(r"(?:greater than|more than|above|over) (\w+)", blob)
        if inner and (value := _word_to_int(inner.group(1))):
            return lambda n, k=value: _digit_sum(n) > k
        inner = re.search(r"(?:less than|below|under) (\w+)", blob)
        if inner and (value := _word_to_int(inner.group(1))):
            return lambda n, k=value: _digit_sum(n) < k
        inner = re.search(r"divisible by (\w+)", blob)
        if inner and (value := _word_to_int(inner.group(1))):
            return lambda n, k=value: _digit_sum(n) % k == 0
        if "even" in blob:
            return lambda n: _digit_sum(n) % 2 == 0
        if "odd" in blob:
            return lambda n: _digit_sum(n) % 2 == 1
        inner = re.search(r"equals? (\w+)|is (\w+)", blob)
        if inner and (value := next((_word_to_int(g) for g in inner.groups() if g), None)):
            return lambda n, k=value: _digit_sum(n) == k

    match = re.search(r"(?:contains|has|includes) (?:the )?digit (\w+)", blob)
    if match and (value := _word_to_int(match.group(1))) is not None:
        return lambda n, d=str(value): d in str(n)

    match = re.search(r"(?:ends? (?:in|with)|last digit is) (\w+)", blob)
    if match and (value := _word_to_int(match.group(1))) is not None:
        return lambda n, d=str(value): str(n).endswith(d)

    match = re.search(r"(?:starts? with|begins with|first digit is) (\w+)", blob)
    if match and (value := _word_to_int(match.group(1))) is not None:
        return lambda n, d=str(value): str(n).startswith(d)

    match = re.search(r"between (\w+) and (\w+)", blob)
    if match:
        low, high = _word_to_int(match.group(1)), _word_to_int(match.group(2))
        if low is not None and high is not None:
            return lambda n, a=low, b=high: a <= n <= b

    match = re.search(r"(?:greater than|more than|above|over|bigger than) (\w+)", blob)
    if match and (value := _word_to_int(match.group(1))) is not None:
        return lambda n, k=value: n > k

    match = re.search(r"(?:less than|below|under|smaller than) (\w+)", blob)
    if match and (value := _word_to_int(match.group(1))) is not None:
        return lambda n, k=value: n < k

    simple: list[tuple[tuple[str, ...], Callable[[int], bool]]] = [
        (("prime",), _is_prime),
        (("composite",), lambda n: n > 1 and not _is_prime(n)),
        (("perfect square", "square number", "is a square", "squares"), lambda n: n in _SQUARES),
        (("perfect cube", "cube number", "is a cube"), lambda n: n in _CUBES),
        (("triangular",), lambda n: n in _TRIANGULAR),
        (("fibonacci",), lambda n: n in _FIBONACCI),
        (("power of two", "power of 2"), lambda n: n in _POWERS_OF_TWO),
        (("palindrome",), lambda n: str(n) == str(n)[::-1]),
        (("two-digit", "two digit"), lambda n: 10 <= n <= 99),
        (("single digit", "one digit", "single-digit"), lambda n: n < 10),
        (("even",), _EVEN),
        (("odd",), _ODD),
    ]
    for needles, predicate in simple:
        if any(needle in blob for needle in needles):
            # Правило о цифрах, не разобранное выше, не должно проваливаться в
            # проверку самого числа: «сумма цифр чётна» и «число чётно» — разные
            # правила, и неверный предикат отсеет верное правило. Признать текст
            # непонятым безопаснее, чем понять его неправильно.
            if "digit" in blob and predicate in (_EVEN, _ODD):
                return None
            return predicate
    return None


@register
class RuleBrain(Brain):
    game = "rule"

    def __init__(self) -> None:
        self.cache: dict[str, Callable[[int], bool] | None] = {}

    def _predicates(self, rules: list[dict]) -> dict[str, Callable[[int], bool] | None]:
        for entry in rules:
            rule_id = str(entry.get("id"))
            if rule_id not in self.cache:
                self.cache[rule_id] = predicate_for(rule_id, str(entry.get("text") or ""))
        return {str(e.get("id")): self.cache[str(e.get("id"))] for e in rules}

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        phase = state.get("phase")
        rules = list(state.get("rules") or [])
        if not rules:
            return None
        predicates = self._predicates(rules)

        if phase == "choose" and state.get("role") == "picker":
            return {"type": "choose_rule", "rule": self._hardest_rule(rules, predicates, ctx)}

        if phase != "probe" or state.get("role") != "guesser":
            return None

        probes = [(int(p["n"]), bool(p["yes"])) for p in (state.get("probes") or []) if "n" in p]
        consistent = []
        unreadable = []
        for entry in rules:
            rule_id = str(entry.get("id"))
            predicate = predicates.get(rule_id)
            if predicate is None:
                unreadable.append(rule_id)
                continue
            if all(predicate(n) == yes for n, yes in probes):
                consistent.append(rule_id)

        probes_left = int(state.get("probesLeft") or 0)

        if len(consistent) == 1:
            return {"type": "guess", "rule": consistent[0]}
        if not consistent:
            # Всё, что мы умеем читать, отсеяно; откатываемся к чему-то, что не
            # смогли разобрать, вместо того чтобы вовсе отказаться от догадки.
            if unreadable:
                return {"type": "guess", "rule": unreadable[0]}
            return {"type": "guess", "rule": str(rules[0].get("id"))}
        if probes_left <= 0:
            return {"type": "guess", "rule": ctx.rng.choice(consistent)}

        probe = self._best_probe(consistent, predicates, {n for n, _ in probes}, ctx)
        return {"type": "probe", "n": probe}

    def _best_probe(self, consistent, predicates, asked, ctx) -> int:
        """Число, которое делит выживших кандидатов наиболее поровну, — проба,
        отсеивающая больше всего кандидатов при любом ответе."""
        best, best_n = -1, None
        for n in DOMAIN:
            if n in asked:
                continue
            yes = sum(1 for rule_id in consistent if predicates[rule_id](n))
            no = len(consistent) - yes
            # Важен худший случай: максимизируем меньшую половину.
            split = min(yes, no)
            if split > best:
                best, best_n = split, n
        if best_n is None or best == 0:
            remaining = [n for n in DOMAIN if n not in asked]
            return ctx.rng.choice(remaining) if remaining else 1
        return best_n

    def _hardest_rule(self, rules, predicates, ctx) -> str:
        """В роли загадывающего выбираем правило, которое легче всего спутать с
        другим: то, чей ответ «да/нет» на числах 1..100 ближе всего к ответу
        какого-то другого правила, — тогда отгадывающему нужно больше всего проб,
        чтобы их разделить."""
        vectors: dict[str, tuple[bool, ...]] = {}
        for entry in rules:
            rule_id = str(entry.get("id"))
            predicate = predicates.get(rule_id)
            if predicate is None:
                continue
            try:
                vectors[rule_id] = tuple(predicate(n) for n in DOMAIN)
            except Exception:
                continue
        if len(vectors) < 2:
            return str(rules[0].get("id"))

        best_id, best_distance = None, 10**9
        for rule_id, vector in vectors.items():
            nearest = min(
                sum(1 for a, b in zip(vector, other) if a != b)
                for other_id, other in vectors.items()
                if other_id != rule_id
            )
            if nearest < best_distance:
                best_distance, best_id = nearest, rule_id
        return best_id or str(rules[0].get("id"))

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        return (
            "Good game. As guesser I drop every rule inconsistent with an answer and probe to split what is "
            "left, since one more probe costs a point and a coin-flip guess costs all of them. "
            "How were you choosing your probes?"
        )

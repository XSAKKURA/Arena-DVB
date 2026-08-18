"""Изучение соперников по их публичным партиям.

Платформа прямо разрешает подготовку: страница `/a/{имя}` открыта и содержит
карточку агента вместе со списком его последних матчей. Здесь из неё
извлекается то, что влияет на решение: сколько партий соперник сыграл в каждой
игре и какую долю выиграл.

Применение одно и вполне определённое. Когда открытых столов несколько, а сесть
можно только за один, выбирается тот, где произведение нашей силы в этой игре на
слабость соперника в ней же максимально. Ни на выбор хода, ни на оценку позиции
разведка не влияет: тенденции соперника внутри партии отслеживаются отдельно,
по ходу игры.

Запросы кешируются: карточка меняется медленно, а лишние обращения расходуют
время, которое нужнее на ходы.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("arena.scout")

#: Сколько считать карточку соперника свежей.
CACHE_SECONDS = 3600.0

#: Сколько партий нужно, чтобы доля побед считалась о чём-то говорящей.
MIN_PLAYS = 3


class Scout:
    def __init__(self, client, store, cache_seconds: float = CACHE_SECONDS):
        self.client = client
        self.store = store
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------ получение

    def record(self, name: str) -> dict:
        """Разбивка партий соперника по играм: {игра: {plays, wins}}.

        Пустой словарь означает «сведений нет» — это не то же самое, что
        «соперник слаб», и обрабатывается вызывающим отдельно.
        """
        if not name or name in ("?", "a human"):
            return {}
        cached = self._cache.get(name)
        now = time.time()
        if cached and now - cached[0] < self.cache_seconds:
            return cached[1]

        try:
            payload = self.client.agent_page(name)
        except Exception as exc:  # noqa: BLE001 — разведка не должна ломать игру
            log.debug("не удалось прочитать карточку %s: %s", name, exc)
            self._cache[name] = (now, {})
            return {}

        record = self._digest(payload, name)
        self._cache[name] = (now, record)
        if record and self.store is not None:
            try:
                known = self.store.read_json("opponent_records.json") or {}
                known[name] = {"at": int(now), "games": record}
                self.store.write_json("opponent_records.json", known)
            except OSError:
                pass
        return record

    @staticmethod
    def _digest(payload: dict, name: str) -> dict:
        matches = (payload or {}).get("matches") or []
        out: dict[str, dict] = {}
        for match in matches:
            game = match.get("game")
            if not game:
                continue
            row = out.setdefault(game, {"plays": 0, "wins": 0})
            row["plays"] += 1
            winners = match.get("winners") or []
            names = [w if isinstance(w, str) else (w or {}).get("name") for w in winners]
            if name in names:
                row["wins"] += 1
        return out

    # -------------------------------------------------------------- оценка

    def win_rate(self, name: str, game: str) -> float | None:
        """Доля побед соперника в этой игре, либо None при недостатке данных."""
        row = self.record(name).get(game)
        if not row or row["plays"] < MIN_PLAYS:
            return None
        return row["wins"] / row["plays"]

    def our_win_rate(self, game: str) -> float | None:
        """Наша доля побед в этой игре по собственному журналу."""
        if self.store is None:
            return None
        row = (self.store.stats or {}).get(game)
        if not row:
            return None
        decided = row.get("win", 0) + row.get("loss", 0) + row.get("draw", 0)
        if decided < MIN_PLAYS:
            return None
        return (row.get("win", 0) + 0.5 * row.get("draw", 0)) / decided

    def table_value(self, game: str, opponent: str) -> float:
        """Ожидаемая ценность стола: наша сила на их слабость.

        Обе составляющие могут быть неизвестны, и тогда используется 0.5 —
        нейтральная оценка, при которой стол не получает ни преимущества, ни
        штрафа. Это важнее, чем кажется: новичок без истории не должен
        выглядеть ни лёгкой добычей, ни угрозой.
        """
        ours = self.our_win_rate(game)
        theirs = self.win_rate(opponent, game)
        return (0.5 if ours is None else ours) * (0.5 if theirs is None else (1.0 - theirs))

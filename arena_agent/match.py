"""Ведение одного матча со своего места за столом.

Модель арены — почтовый ящик, а не сокет: каждое сообщение кладётся в очередь с
порядковым номером, а `since` показывает, докуда мы прочитали. Этот класс хранит
курсор, отдаёт события мозгу по порядку и отправляет то, что мозг решил, — в том
числе несколько ходов подряд, потому что бонусный ход в точках-и-квадратах или
связка «выстрел, затем движение» в танках приходит прямо в ответе на предыдущий
ход и не должна стоить лишнего опроса.

От двух режимов отказа защищаемся явно:

* **Опрос без ходов всё равно ведёт к поражению по времени.** Часы считают ходы,
  а не чтения, поэтому цикл всегда действует, когда `choose` что-то вернул.
* **Отклонённый ход — не ошибка для слепого повтора.** С ним приходят причина и
  свежее состояние; мы решаем заново уже по нему, а после нескольких отказов
  бросаем этот ход, а не жжём квоту ходов в цикле.
"""

from __future__ import annotations

import logging
import time

from .brains import Context, brain_for
from .client import ArenaError, RateLimited, TransportError

log = logging.getLogger("arena.match")

MAX_MOVES_PER_TURN = 80
MAX_REJECTIONS = 4


def outcome_for(result: dict | None, name: str, seat: int | None = None) -> str:
    """win / loss / draw / void / unknown.

    Конец матча арена сообщает внутри события стола, в виде
    `{"winners": ["<имя агента>"], "scores": {...}, "detail": "...", "rated": ...}` —
    победители там указаны *именами*, а не номерами мест, по ним и сверяемся.
    """
    if not isinstance(result, dict):
        return "unknown"
    if result.get("void") or str(result.get("reason", "")).lower() == "void":
        return "void"

    winners = result.get("winners")
    if isinstance(winners, list):
        if not winners:
            return "draw"  # никто не выиграл: ничью арена сообщает как отсутствие победителя
        if any(str(w) == name for w in winners):
            return "win"
        if seat is not None and any(str(w) == str(seat) for w in winners):
            return "win"
        return "loss"

    winner = result.get("winner")
    if winner is not None:
        if str(winner) == name or (seat is not None and str(winner) == str(seat)):
            return "win"
        if isinstance(winner, str) and winner in ("black", "white", "w", "b"):
            return "unknown"
        return "loss"
    if "winner" in result:
        return "draw"

    outcome = str(result.get("outcome") or "")
    if outcome == "1/2-1/2" or result.get("draw"):
        return "draw"
    return "unknown"


class MatchSession:
    """Одно место за одним столом — от посадки до итогового результата."""

    def __init__(self, runner, code: str, game: str, pace: str = "live", mode: str = "ranked"):
        self.runner = runner
        self.client = runner.client
        self.settings = runner.settings
        self.code = code
        self.game = game
        self.pace = pace
        self.mode = mode

        self.brain = brain_for(game)
        self.ctx = Context(
            code=code,
            game=game,
            store=runner.store,
            rng=runner.rng,
            pace=pace,
            mode=mode,
            think_seconds=(
                self.settings.async_think_seconds if pace == "async" else self.settings.live_think_seconds
            ),
        )
        self.since = 0
        self.status = "waiting"
        self.state: dict = {}
        self.finished = False
        self.chat_room: str | None = None
        self.greeted = False
        self.opened_at = time.time()
        self.started_at: float | None = None
        self.next_poll_at = 0.0
        self.idle_polls = 0
        self.moves_sent = 0
        self.last_error: str | None = None
        self.result: dict | None = None

    # -------------------------------------------------------- жизненный цикл

    @property
    def waiting_for_opponent(self) -> bool:
        return self.status == "waiting" and not self.finished

    def _absorb(self, payload: dict) -> None:
        """Обновить всё, что мы отслеживаем, из payload'а матча."""
        if not isinstance(payload, dict):
            return
        if "your_seat" in payload:
            self.ctx.seat = payload["your_seat"]
        if "participants" in payload:
            self.ctx.opponents = [
                p for p in payload["participants"] if str(p.get("seat")) != str(self.ctx.seat)
            ]
        if payload.get("chat_room"):
            self.chat_room = payload["chat_room"]
        if "status" in payload and payload["status"]:
            previous, self.status = self.status, payload["status"]
            if previous == "waiting" and self.status == "playing":
                self.started_at = time.time()
        if "next_since" in payload:
            self.since = payload["next_since"]
        if isinstance(payload.get("state"), dict):
            self.state = payload["state"]
        if payload.get("move_deadline_at"):
            self.ctx.deadline_at = float(payload["move_deadline_at"]) / 1000.0
        self.ctx.meta = {
            "rules_version": payload.get("rules_version"),
            "rated": payload.get("rated"),
            "reserve_seconds": payload.get("reserve_seconds"),
        }
        if payload.get("gap"):
            log.info("[%s] отстали от почтового ящика — восстанавливаемся из state", self.code)

    def _dispatch(self, payload: dict) -> None:
        """Отдать мозгу каждое игровое событие, по порядку."""
        for event in payload.get("events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("kind") == "game":
                data = event.get("data")
                if isinstance(data, dict) and self.brain:
                    try:
                        self.brain.on_event(data, self.ctx)
                    except Exception:
                        log.exception("[%s] brain.on_event упал", self.code)
            elif event.get("kind") == "table":
                if event.get("status"):
                    self.status = event["status"]
                if event.get("participants"):
                    self.ctx.opponents = [
                        p
                        for p in event["participants"]
                        if str(p.get("seat")) != str(self.ctx.seat)
                    ]
                # Итоговый результат приходит именно сюда и больше никуда.
                if isinstance(event.get("result"), dict):
                    self.result = event["result"]

    # -------------------------------------------------------------- обслуга

    def service(self) -> bool:
        """Один квант работы. Возвращает True, если что-то действительно произошло."""
        if self.finished:
            return False
        try:
            payload = self.client.match(self.code, self.since)
        except RateLimited as exc:
            self.next_poll_at = time.time() + exc.retry_after
            return False
        except ArenaError as exc:
            return self._handle_gone(exc)
        except TransportError as exc:
            log.warning("[%s] чтение не удалось: %s", self.code, exc)
            self.next_poll_at = time.time() + 5
            return False

        had_events = bool(payload.get("events"))
        self._absorb(payload)
        self._dispatch(payload)

        if not had_events:
            self.client.note_empty_read()

        if self.status in ("finished", "over", "void", "abandoned"):
            self._finish(payload)
            return True

        if self.status == "waiting":
            # Делать нечего, надо просто оставаться в эфире; место держит
            # runner через GET /api/tables, который не тарифицируется.
            self.next_poll_at = time.time() + self.settings.waiting_poll_seconds
            return False

        self._greet()
        acted = self._play_turn()

        if acted or had_events:
            self.idle_polls = 0
        else:
            self.idle_polls += 1
        self._schedule_next_poll()
        return acted or had_events

    def _play_turn(self) -> bool:
        """Слать ходы, пока у мозга есть что слать."""
        if not self.brain:
            return False
        acted = False
        rejections = 0

        for _ in range(MAX_MOVES_PER_TURN):
            try:
                move = self.brain.choose(self.state, self.ctx)
            except Exception:
                log.exception("[%s] brain.choose упал на %s", self.code, self.game)
                return acted
            if not move:
                return acted

            try:
                reply = self.client.move(self.code, move)
            except RateLimited as exc:
                self.next_poll_at = time.time() + exc.retry_after
                return acted
            except ArenaError as exc:
                if self._handle_gone(exc):
                    return True
                log.warning("[%s] ход отклонён: %s", self.code, exc.message)
                return acted
            except TransportError as exc:
                # Ход мог дойти, а мог и нет, — дважды его слать нельзя.
                log.warning("[%s] ход мог не отправиться (%s); перечитываем", self.code, exc)
                self.next_poll_at = time.time() + 2
                return acted

            self._absorb(reply)
            self._dispatch(reply)

            if reply.get("accepted") is False:
                rejections += 1
                self.last_error = str(reply.get("reason") or "")
                log.warning(
                    "[%s] %s отклонил %s: %s", self.code, self.game, move, self.last_error[:160]
                )
                if rejections >= MAX_REJECTIONS:
                    log.error("[%s] бросаем этот ход после %d отказов", self.code, rejections)
                    return acted
                continue

            acted = True
            self.moves_sent += 1
            log.info("[%s] %s сыграл %s", self.code, self.game, _short(move))

            if self.status in ("finished", "over", "void", "abandoned"):
                self._finish(reply)
                return True
        return acted

    def _schedule_next_poll(self) -> None:
        now = time.time()
        if self.pace == "async":
            # Заочные столы не опрашиваются по одному: runner обходит
            # GET /api/my/turns, который одним запросом покрывает все столы,
            # за которыми мы сидим, и будит те, что ждут нашего хода.
            # Семь столов по отдельности съели бы всю дневную квоту пустых
            # чтений на молчание.
            self.next_poll_at = now + 3600.0
            return
        low = self.settings.live_poll_min_seconds
        high = self.settings.live_poll_max_seconds
        delay = min(high, low * (1.6**self.idle_polls))
        # Тратить меньше квоты чтений, когда она подходит к концу.
        headroom = self.client.empty_read_headroom
        if headroom < 0.35:
            delay = min(high, delay * 2.5)
        # Никогда не спать дольше собственного дедлайна на ход.
        if self.ctx.deadline_at:
            left = self.ctx.deadline_at - now
            if left > 0:
                delay = min(delay, max(low, left * self.settings.deadline_safety_margin))
        self.next_poll_at = now + delay

    # -------------------------------------------------------------- завершение

    def _handle_gone(self, exc: ArenaError) -> bool:
        """404/410 и подобные: арена сообщает, что место уже не наше."""
        if exc.status in (404, 410, 409, 403):
            log.info("[%s] место закрыто ареной: %s", self.code, exc.message[:200])
            self._finish({"result": {"reason": exc.message}}, note=exc.message)
            return True
        log.warning("[%s] ошибка арены %s: %s", self.code, exc.status, exc.message[:200])
        self.next_poll_at = time.time() + 10
        return False

    def _greet(self) -> None:
        if self.greeted or not self.chat_room or not self.brain:
            return
        self.greeted = True
        line = None
        try:
            line = self.brain.greeting(self.ctx)
        except Exception:
            line = None
        if line is None:
            line = (
                f"Hello — {self.runner.agent_name} here, sitting down for {self.game}. "
                f"Good luck. I will say what I was running when we are done."
            )
        self.runner.chat.say(self.chat_room, line)

    def _finish(self, payload: dict, note: str | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        if isinstance(payload.get("result"), dict):
            self.result = payload["result"]
        result = self.result or {}
        outcome = outcome_for(result, self.runner.agent_name, self.ctx.seat)

        log.info(
            "[%s] %s закончен: %s — %s (%d ходов%s)",
            self.code,
            self.game,
            outcome,
            note or result.get("detail") or result.get("reason") or "no detail",
            self.moves_sent,
            ", рейтинговый" if result.get("rated") else "",
        )
        self.runner.store.record_result(self.game, outcome)
        self.runner.store.journal(
            {
                "code": self.code,
                "game": self.game,
                "pace": self.pace,
                "mode": self.mode,
                "outcome": outcome,
                "moves": self.moves_sent,
                "opponent": self.ctx.opponent_name,
                "rated": bool(result.get("rated")),
                "detail": result.get("detail"),
                "scores": result.get("scores"),
                "url": f"{self.settings.arena_url}/m/{self.code}",
            }
        )

        if self.chat_room and self.brain and self.moves_sent:
            try:
                line = self.brain.on_finish(self.state, self.ctx)
            except Exception:
                line = None
            if line:
                self.runner.chat.say(self.chat_room, line)

    def resign(self) -> None:
        if self.finished:
            return
        try:
            self.client.resign(self.code)
            log.info("[%s] встали из-за стола", self.code)
        except (ArenaError, TransportError) as exc:
            log.debug("[%s] не удалось сдаться: %s", self.code, exc)
        self.finished = True


def _short(move: dict) -> str:
    text = str(move)
    return text if len(text) <= 120 else text[:117] + "..."

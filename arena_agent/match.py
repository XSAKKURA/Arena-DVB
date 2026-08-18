"""Driving one match from the seat.

The arena's model is a mailbox, not a socket: every message is queued with a
sequence number and `since` is how far we have read. This class keeps that
cursor, hands events to the brain in order, and sends whatever the brain
decides — including several moves in a row, because a bonus move in dots and
boxes or the shoot-then-move turn in tanks arrives inside the reply to the
previous move and must not cost an extra poll.

Two failure modes are designed against explicitly:

* **Polling that never moves still forfeits.** The clock counts moves, not
  reads, so the loop always acts when `choose` returns something.
* **A rejected move is not an error to retry blindly.** It carries a reason and
  a fresh state; we re-decide from that state, and give up on the turn after a
  few refusals rather than burning the move budget in a loop.
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

    The arena reports the end of a match inside a table event, as
    `{"winners": ["<agent name>"], "scores": {...}, "detail": "...", "rated": ...}`
    — winners are *names*, not seat ids, so that is what we match on.
    """
    if not isinstance(result, dict):
        return "unknown"
    if result.get("void") or str(result.get("reason", "")).lower() == "void":
        return "void"

    winners = result.get("winners")
    if isinstance(winners, list):
        if not winners:
            return "draw"  # nobody won: the arena reports a draw as no winner
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
    """One seat at one table, from sitting down to the final result."""

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

    # ------------------------------------------------------------ lifecycle

    @property
    def waiting_for_opponent(self) -> bool:
        return self.status == "waiting" and not self.finished

    def _absorb(self, payload: dict) -> None:
        """Update everything we track from a match payload."""
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
            log.info("[%s] fell behind the mailbox — rebuilding from state", self.code)

    def _dispatch(self, payload: dict) -> None:
        """Hand the brain every game event, in order."""
        for event in payload.get("events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("kind") == "game":
                data = event.get("data")
                if isinstance(data, dict) and self.brain:
                    try:
                        self.brain.on_event(data, self.ctx)
                    except Exception:
                        log.exception("[%s] brain.on_event failed", self.code)
            elif event.get("kind") == "table":
                if event.get("status"):
                    self.status = event["status"]
                if event.get("participants"):
                    self.ctx.opponents = [
                        p
                        for p in event["participants"]
                        if str(p.get("seat")) != str(self.ctx.seat)
                    ]
                # The final result arrives here and nowhere else.
                if isinstance(event.get("result"), dict):
                    self.result = event["result"]

    # ---------------------------------------------------------------- serve

    def service(self) -> bool:
        """One slice of work. Returns True if anything actually happened."""
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
            log.warning("[%s] read failed: %s", self.code, exc)
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
            # Nothing to do but stay on the air; the runner keeps the seat
            # alive with GET /api/tables, which is not metered.
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
        """Send moves for as long as the brain has something to send."""
        if not self.brain:
            return False
        acted = False
        rejections = 0

        for _ in range(MAX_MOVES_PER_TURN):
            try:
                move = self.brain.choose(self.state, self.ctx)
            except Exception:
                log.exception("[%s] brain.choose failed for %s", self.code, self.game)
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
                log.warning("[%s] move refused: %s", self.code, exc.message)
                return acted
            except TransportError as exc:
                # The move may or may not have landed — never send it twice.
                log.warning("[%s] move may not have been sent (%s); re-reading", self.code, exc)
                self.next_poll_at = time.time() + 2
                return acted

            self._absorb(reply)
            self._dispatch(reply)

            if reply.get("accepted") is False:
                rejections += 1
                self.last_error = str(reply.get("reason") or "")
                log.warning(
                    "[%s] %s rejected %s: %s", self.code, self.game, move, self.last_error[:160]
                )
                if rejections >= MAX_REJECTIONS:
                    log.error("[%s] giving up on this turn after %d refusals", self.code, rejections)
                    return acted
                continue

            acted = True
            self.moves_sent += 1
            log.info("[%s] %s played %s", self.code, self.game, _short(move))

            if self.status in ("finished", "over", "void", "abandoned"):
                self._finish(reply)
                return True
        return acted

    def _schedule_next_poll(self) -> None:
        now = time.time()
        if self.pace == "async":
            # Correspondence tables are not polled one by one — the runner
            # sweeps GET /api/my/turns, which covers every table we sit at in
            # a single request, and wakes the ones that are waiting on us.
            # Seven tables polled individually would spend the whole daily
            # empty-read allowance on silence.
            self.next_poll_at = now + 3600.0
            return
        low = self.settings.live_poll_min_seconds
        high = self.settings.live_poll_max_seconds
        delay = min(high, low * (1.6**self.idle_polls))
        # Spend less of the read budget when it is running short.
        headroom = self.client.empty_read_headroom
        if headroom < 0.35:
            delay = min(high, delay * 2.5)
        # Never sleep past our own move deadline.
        if self.ctx.deadline_at:
            left = self.ctx.deadline_at - now
            if left > 0:
                delay = min(delay, max(low, left * self.settings.deadline_safety_margin))
        self.next_poll_at = now + delay

    # --------------------------------------------------------------- ending

    def _handle_gone(self, exc: ArenaError) -> bool:
        """404/410 and friends: the arena is telling us the seat is not ours."""
        if exc.status in (404, 410, 409, 403):
            log.info("[%s] seat closed by the arena: %s", self.code, exc.message[:200])
            self._finish({"result": {"reason": exc.message}}, note=exc.message)
            return True
        log.warning("[%s] arena error %s: %s", self.code, exc.status, exc.message[:200])
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
            "[%s] %s finished: %s — %s (%d moves%s)",
            self.code,
            self.game,
            outcome,
            note or result.get("detail") or result.get("reason") or "no detail",
            self.moves_sent,
            ", rated" if result.get("rated") else "",
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
            log.info("[%s] left the table", self.code)
        except (ArenaError, TransportError) as exc:
            log.debug("[%s] resign failed: %s", self.code, exc)
        self.finished = True


def _short(move: dict) -> str:
    text = str(move)
    return text if len(text) <= 120 else text[:117] + "..."

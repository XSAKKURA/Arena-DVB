"""The orchestrator: what keeps the agent playing everything, always.

The arena imposes the shape of this. **One key, one live table** — a live match
wants you present, and an agent at three of them forfeits two. Correspondence
tables are the opposite: up to eight at once, hours per move, and they survive
a restart of the station. So "play all the games, always" is not one loop, it
is two lanes that share a key:

* the **correspondence lane** keeps a table open in as many `pace:"async"`
  games as the arena allows, which is the backbone — those matches continue
  across restarts and cost almost nothing to hold;
* the **live lane** holds exactly one live table at a time and rotates it
  through every other game, so the games that cannot be played by post still
  get played.

Everything is one cooperative thread. A live match with a fifteen-minute move
deadline can easily afford the milliseconds it takes to check the other lane,
and a single thread means no lock ever stands between us and a move.
"""

from __future__ import annotations

import logging
import random
import time

from .brains import known_games
from .chat import ChatClient
from .client import ArenaClient, ArenaError, RateLimited, TransportError
from .config import MIN_SEATS, Settings
from .match import MatchSession
from .store import Store

log = logging.getLogger("arena.runner")

DISCOVERY_INTERVAL = 20.0
STATUS_INTERVAL = 300.0


class Runner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.state_dir)
        self.client = ArenaClient(settings)
        self.rng = random.Random()
        self.agent_name = settings.agent_name
        self.chat = ChatClient(
            settings.roomcomm_url, settings.agent_name, self.store, enabled=settings.enable_chat
        )

        self.sessions: dict[str, MatchSession] = {}
        self.games_meta: dict[str, dict] = {}
        self.async_games: list[str] = []
        self.live_games: list[str] = []
        self.rotation = 0
        self.consecutive_empty_waits = 0
        self.next_discovery = 0.0
        self.next_status = 0.0
        self.stopping = False
        self._bootstrapped = False
        self.live_cooldown_until = 0.0

    # ------------------------------------------------------------ bootstrap

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        self._bootstrapped = True
        self._ensure_key()
        self._load_catalogue()
        self._declare()
        self._recover_seats()

    def _ensure_key(self) -> None:
        record = self.store.load_key()
        if record and record.get("key"):
            self.client.key = record["key"]
            try:
                me = self.client.me()
            except ArenaError as exc:
                raise SystemExit(
                    f"the saved key was refused by the arena ({exc.message}). "
                    "Remove it from the state directory to register a new one."
                ) from None
            self.agent_name = me.get("name") or self.settings.agent_name
            self.chat.agent_id = self.agent_name
            log.info(
                "playing as %s (owner %s), rating %s over %s matches, tier %s",
                self.agent_name,
                me.get("owner"),
                me.get("rating"),
                me.get("plays"),
                me.get("tier"),
            )
            budget = me.get("daily_budget") or {}
            self.settings.daily_empty_reads = int(budget.get("empty_reads", self.settings.daily_empty_reads))
            self.settings.daily_moves = int(budget.get("moves", self.settings.daily_moves))
            self.settings.daily_tables = int(budget.get("tables", self.settings.daily_tables))
            return

        log.info("no key on file — registering %s", self.settings.agent_name)
        created = self.client.register(
            self.settings.agent_name, self.settings.owner, self.settings.runtime, self.settings.model
        )
        self.client.key = created["key"]
        self.agent_name = created.get("name", self.settings.agent_name)
        self.chat.agent_id = self.agent_name
        self.store.save_key(created)
        log.info("registered as %s — the key is in %s and is shown only once", self.agent_name, self.store.dir)

    def _load_catalogue(self) -> None:
        try:
            catalogue = self.client.games()
        except (ArenaError, TransportError) as exc:
            log.warning("could not read the game catalogue (%s) — using built-in defaults", exc)
            catalogue = []
        self.games_meta = {g["id"]: g for g in catalogue if g.get("id")}

        playable = set(known_games()) & set(self.settings.games)
        if self.games_meta:
            playable &= set(self.games_meta)
        # Only sit down where we know the game — the arena asks for exactly
        # this, and an agent playing gomoku with rock-paper-scissors logic
        # wastes a stranger's match as well as its own.
        self.async_games = [g for g in self.settings.games if g in playable and self._is_async(g)]
        self.live_games = [g for g in self.settings.games if g in playable]
        log.info(
            "%d games playable (%d by correspondence): %s",
            len(self.live_games),
            len(self.async_games),
            ", ".join(self.live_games),
        )

    def _is_async(self, game: str) -> bool:
        meta = self.games_meta.get(game)
        if meta is not None:
            return bool(meta.get("async"))
        from .config import ASYNC_GAMES

        return game in ASYNC_GAMES

    def _has_bot(self, game: str) -> bool:
        meta = self.games_meta.get(game)
        if meta is not None:
            return bool(meta.get("practice_bot"))
        from .config import PRACTICE_BOT_GAMES

        return game in PRACTICE_BOT_GAMES

    def _is_solo(self, game: str) -> bool:
        meta = self.games_meta.get(game)
        if meta is not None:
            return bool(meta.get("solo"))
        from .config import SOLO_GAMES

        return game in SOLO_GAMES

    def _declare(self) -> None:
        """Say what we run on. It is a self-description, not a credential, but
        it is what makes "which model plays chess better" answerable."""
        try:
            self.client.declare(runtime=self.settings.runtime, model=self.settings.model)
        except (ArenaError, TransportError) as exc:
            log.debug("declare failed: %s", exc)

    def _recover_seats(self) -> None:
        """After a restart, go back to the tables we are still sitting at
        rather than opening new ones."""
        try:
            me = self.client.me()
        except (ArenaError, TransportError):
            return
        codes: list[tuple[str, str]] = []
        for seat in me.get("seats") or []:
            if isinstance(seat, dict) and seat.get("code"):
                codes.append((seat["code"], seat.get("game", "")))
            elif isinstance(seat, str):
                codes.append((seat, ""))
        if me.get("seated_at"):
            codes.append((me["seated_at"], ""))

        try:
            turns = self.client.my_turns()
            for entry in (turns.get("turns") or []) + (turns.get("waiting") or []):
                if isinstance(entry, dict) and entry.get("code"):
                    codes.append((entry["code"], entry.get("game", "")))
        except (ArenaError, TransportError):
            pass

        for code, game in codes:
            if code in self.sessions:
                continue
            self._adopt(code, game)
        if self.sessions:
            log.info("resumed %d table(s) from before the restart: %s", len(self.sessions), list(self.sessions))

    def _adopt(
        self, code: str, game: str = "", pace: str = "", mode: str = "ranked", fresh: bool = False
    ) -> MatchSession | None:
        """Attach a session to a table we hold a seat at. `fresh` means we have
        only just sat down, so we still owe the table a hello."""
        try:
            payload = self.client.match(code, 0)
        except (ArenaError, TransportError) as exc:
            log.debug("cannot adopt %s: %s", code, exc)
            return None
        game = payload.get("game") or game
        if not game:
            return None
        if game not in set(known_games()):
            log.warning("seated at %s (%s) with no brain for it — resigning", code, game)
            try:
                self.client.resign(code)
            except (ArenaError, TransportError):
                pass
            return None
        session = MatchSession(
            self,
            code,
            game,
            pace=payload.get("pace") or pace or "live",
            mode=payload.get("mode") or mode,
        )
        session._absorb(payload)
        session._dispatch(payload)
        # Rejoining a table we were already at means we said hello there once
        # already; repeating it on every restart is noise in somebody's room.
        session.greeted = not fresh
        self.sessions[code] = session
        return session

    # ----------------------------------------------------------------- lanes

    @property
    def live_sessions(self) -> list[MatchSession]:
        return [s for s in self.sessions.values() if s.pace != "async" and not s.finished]

    @property
    def async_sessions(self) -> list[MatchSession]:
        return [s for s in self.sessions.values() if s.pace == "async" and not s.finished]

    def _tables_left(self) -> int:
        return max(0, self.settings.daily_tables - self.client.tables_opened)

    def _budget_ok_for_table(self, lane: str = "async") -> bool:
        """Opening a table is metered (60 a day on the free tier). The live
        lane is the one that can run through that on its own — a table nobody
        joins is abandoned after a few minutes and replaced — so it is held
        behind a reserve that only correspondence tables may spend."""
        left = self._tables_left()
        if lane == "live":
            return left > self.settings.async_table_reserve
        return left > 2

    def _live_pace_seconds(self) -> float:
        """How long to wait between opening live tables, so the lane's share of
        the daily allowance is spread across the day instead of spent in the
        first few hours."""
        spendable = self._tables_left() - self.settings.async_table_reserve
        if spendable <= 0:
            return 3600.0
        now = time.time()
        seconds_to_reset = 86400 - (now % 86400)  # the budget resets at UTC midnight
        return max(self.settings.live_wait_seconds, seconds_to_reset / spendable)

    def _next_game(self, pool: list[str], exclude: set[str]) -> str | None:
        """Round-robin by least recently seated, so no game starves."""
        options = [g for g in pool if g not in exclude]
        if not options:
            return None
        options.sort(key=lambda g: (self.store.last_seated(g), g))
        return options[0]

    def discover(self) -> None:
        """One authenticated read that does three jobs: it keeps every waiting
        seat of ours alive, it is explicitly not metered as an empty read, and
        it tells us which tables we could join instead of opening our own."""
        try:
            tables = self.client.tables()
        except RateLimited:
            return
        except (ArenaError, TransportError) as exc:
            log.debug("table listing failed: %s", exc)
            return

        joinable = []
        for table in tables:
            code = table.get("code")
            if not code or code in self.sessions:
                continue
            if table.get("status") not in (None, "waiting"):
                continue
            game = table.get("game")
            if game not in self.live_games:
                continue
            participants = table.get("participants") or []
            if any(p.get("name") == self.agent_name for p in participants):
                continue
            if int(table.get("seats_taken") or 0) >= int(table.get("seats_wanted") or 2):
                continue
            joinable.append(table)

        for table in joinable:
            pace = table.get("pace") or "live"
            if pace == "async":
                if len(self.async_sessions) >= self.settings.max_async_tables:
                    continue
            elif self.live_sessions:
                continue
            # Joining is not paced the way opening is: somebody is already
            # sitting there, so this starts a real game immediately instead of
            # spending a table on a seat that may go unanswered.
            if not self._budget_ok_for_table("async" if pace == "async" else "live"):
                continue
            if self._join(table):
                break

    def _join(self, table: dict) -> bool:
        code = table["code"]
        game = table["game"]
        try:
            self.client.join_table(code)
        except ArenaError as exc:
            log.debug("could not join %s (%s): %s", code, game, exc.message[:120])
            return False
        except TransportError as exc:
            log.debug("join failed: %s", exc)
            return False
        log.info("joined %s at table %s (%s)", game, code, table.get("pace") or "live")
        self.store.note_played(game)
        session = self._adopt(
            code, game, table.get("pace") or "live", table.get("mode") or "ranked", fresh=True
        )
        return session is not None

    def ensure_async_lane(self) -> None:
        if not self.settings.enable_async_lane or not self.async_games:
            return
        open_slots = self.settings.max_async_tables - len(self.async_sessions)
        if open_slots <= 0 or not self._budget_ok_for_table():
            return
        busy = {s.game for s in self.async_sessions}
        game = self._next_game(self.async_games, busy)
        if not game:
            return
        self._open_table(game, pace="async", move_hours=self.settings.async_move_hours)

    def ensure_live_lane(self) -> None:
        if not self.settings.enable_live_lane or not self.live_games:
            return
        live = self.live_sessions
        if live:
            self._reap_stale_waits(live)
            return
        if not self._budget_ok_for_table("live"):
            return
        # A table that produced a real match was a table well spent, so the
        # next one opens straight away. Only a seat nobody took starts a
        # cooldown, because that is the pattern that burns the allowance.
        if time.time() < self.live_cooldown_until:
            return

        game = self._next_game(self.live_games, set())
        if not game:
            return

        # If several ranked tables in a row have gone unanswered, the arena is
        # simply quiet — play the station bot rather than sit there, so the
        # agent keeps playing instead of keeping a seat warm.
        mode = "ranked"
        if self.consecutive_empty_waits >= 2:
            bots = [g for g in self.live_games if self._has_bot(g) or self._is_solo(g)]
            if bots:
                game = self._next_game(bots, set()) or game
                mode = "practice"
        self._open_table(game, mode=mode)

    def _open_table(self, game: str, mode: str = "ranked", pace: str = "live", move_hours: int | None = None) -> None:
        seats = MIN_SEATS.get(game)
        meta = self.games_meta.get(game) or {}
        if not seats and isinstance(meta.get("seats"), dict):
            seats = meta["seats"].get("default")
        try:
            payload = self.client.create_table(
                game, mode=mode, pace=pace if pace != "live" else None,
                move_hours=move_hours, seats=seats,
            )
        except ArenaError as exc:
            if exc.status == 409:
                # Already seated somewhere the arena knows about and we do not.
                code = _code_from_message(exc.message)
                if code and code not in self.sessions:
                    log.info("arena says we are already at %s — adopting it", code)
                    self._adopt(code)
                return
            log.warning("could not open %s table for %s: %s", mode, game, exc.message[:160])
            return
        except TransportError as exc:
            log.warning("could not open table: %s", exc)
            return

        table = payload.get("table") if isinstance(payload.get("table"), dict) else payload
        code = table.get("code")
        if not code:
            log.warning("table creation returned no code: %s", str(payload)[:200])
            return
        log.info(
            "opened %s %s table for %s at %s%s",
            pace,
            mode,
            game,
            code,
            f" ({move_hours}h a move)" if move_hours else "",
        )
        self.store.note_played(game)
        session = MatchSession(self, code, game, pace=pace, mode=mode)
        session._absorb(table if "status" in table else payload)
        self.sessions[code] = session

    def _reap_stale_waits(self, live: list[MatchSession]) -> None:
        """A live table nobody joins is a game we are not playing. Give it a
        few minutes, then take the seat elsewhere."""
        now = time.time()
        for session in live:
            if not session.waiting_for_opponent:
                continue
            if now - session.opened_at < self.settings.live_wait_seconds:
                continue
            log.info(
                "[%s] nobody sat down for %s in %.0fs — freeing the seat",
                session.code,
                session.game,
                now - session.opened_at,
            )
            session.resign()
            self.consecutive_empty_waits += 1
            self.live_cooldown_until = now + self._live_pace_seconds()
            self.sessions.pop(session.code, None)

    # ------------------------------------------------------------------ run

    def tick(self) -> float:
        """One pass. Returns how long the caller may sleep."""
        now = time.time()

        for session in list(self.sessions.values()):
            if session.finished:
                self.sessions.pop(session.code, None)
                continue
            if now >= session.next_poll_at:
                try:
                    session.service()
                except Exception:
                    log.exception("[%s] session failed; dropping it", session.code)
                    self.sessions.pop(session.code, None)
                if session.finished:
                    self.sessions.pop(session.code, None)
                    if session.pace != "async":
                        self.consecutive_empty_waits = 0

        if now >= self.next_discovery:
            self.next_discovery = now + DISCOVERY_INTERVAL
            self.discover()
            self.ensure_async_lane()
            self.ensure_live_lane()

        if now >= self.next_status:
            self.next_status = now + STATUS_INTERVAL
            self._log_status()

        hold = self.client.blocked_for
        if hold > 0:
            return min(hold, 30.0)
        upcoming = [s.next_poll_at for s in self.sessions.values() if not s.finished]
        upcoming.append(self.next_discovery)
        return max(0.25, min(min(upcoming) - time.time(), 15.0))

    def _log_status(self) -> None:
        live = self.live_sessions
        async_ = self.async_sessions
        log.info(
            "status: %d live (%s), %d correspondence (%s) | spent today: %d moves, %d tables, %d empty reads of %d",
            len(live),
            ", ".join(f"{s.game}:{s.status}" for s in live) or "-",
            len(async_),
            ", ".join(f"{s.game}:{s.status}" for s in async_) or "-",
            self.client.moves_spent,
            self.client.tables_opened,
            self.client.empty_reads,
            self.settings.daily_empty_reads,
        )

    def run_forever(self) -> None:
        self.bootstrap()
        log.info("running — the agent will keep playing until it is stopped")
        while not self.stopping:
            try:
                sleep_for = self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("tick failed; continuing")
                sleep_for = 5.0
            time.sleep(max(0.1, sleep_for))

    def shutdown(self, resign_live: bool = False) -> None:
        """Correspondence tables are left exactly as they are: they survive a
        restart by design and the clock is hours wide. A live table is the one
        that costs an opponent their time, so it can be given up on request."""
        self.stopping = True
        if not resign_live:
            log.info("leaving %d table(s) in place; the same key can come back to them", len(self.sessions))
            return
        for session in self.live_sessions:
            session.resign()


def _code_from_message(message: str) -> str | None:
    import re

    match = re.search(r"\bmatch ([A-Z0-9]{6,10})\b", message)
    return match.group(1) if match else None

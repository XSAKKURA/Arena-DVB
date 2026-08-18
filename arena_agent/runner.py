"""Оркестратор: то, что заставляет агента играть во всё и всегда.

Форму задаёт сама арена. **Один ключ — один живой стол**: живой матч требует
присутствия, и агент за тремя столами проигрывает два по времени. Заочные столы
устроены наоборот: до восьми сразу, часы на ход, и они переживают перезапуск
станции. Поэтому «играть во все игры всегда» — это не один цикл, а две линии,
делящие один ключ:

* **заочная линия** держит открытый стол в стольких играх с `pace:"async"`,
  сколько разрешает арена, — это костяк: такие матчи продолжаются через
  перезапуски и почти ничего не стоят в удержании;
* **живая линия** держит ровно один живой стол за раз и гоняет его по кругу
  через все остальные игры, чтобы те, в которые нельзя играть по переписке,
  тоже игрались.

Всё это — один кооперативный поток. Живой матч с пятнадцатиминутным дедлайном на
ход спокойно переживёт те миллисекунды, что уходят на проверку второй линии, а
единственный поток означает, что между нами и ходом никогда не стоит блокировка.
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
        self.consecutive_empty_waits = 0
        self.next_discovery = 0.0
        self.next_sweep = 0.0
        self.next_status = 0.0
        self.stopping = False
        self._bootstrapped = False
        self.live_cooldown_until = 0.0

    # ------------------------------------------------------------- запуск

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
                    f"арена отвергла сохранённый ключ ({exc.message}). "
                    "Удалите его из каталога состояния, чтобы зарегистрировать новый."
                ) from None
            self.agent_name = me.get("name") or self.settings.agent_name
            self.chat.agent_id = self.agent_name
            log.info(
                "играем как %s (владелец %s), рейтинг %s за %s матчей, тариф %s",
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

        log.info("ключа на диске нет — регистрируем %s", self.settings.agent_name)
        created = self.client.register(
            self.settings.agent_name, self.settings.owner, self.settings.runtime, self.settings.model
        )
        self.client.key = created["key"]
        self.agent_name = created.get("name", self.settings.agent_name)
        self.chat.agent_id = self.agent_name
        self.store.save_key(created)
        log.info("зарегистрированы как %s — ключ лежит в %s и показывается только один раз", self.agent_name, self.store.dir)

    def _load_catalogue(self) -> None:
        try:
            catalogue = self.client.games()
        except (ArenaError, TransportError) as exc:
            log.warning("не удалось прочитать каталог игр (%s) — берём встроенные значения", exc)
            catalogue = []
        self.games_meta = {g["id"]: g for g in catalogue if g.get("id")}

        playable = set(known_games()) & set(self.settings.games)
        if self.games_meta:
            playable &= set(self.games_meta)
        # Садиться только туда, где знаем игру: арена просит ровно об этом, а
        # агент, играющий в гомоку логикой камень-ножницы-бумага, тратит впустую
        # не только свой матч, но и чужой.
        if self.settings.rated_only:
            unrated = {g for g in playable if not self._is_rated(g)}
            if unrated:
                log.info("пропускаем нерейтинговые игры: %s", ", ".join(sorted(unrated)))
            playable -= unrated
        self.async_games = [g for g in self.settings.games if g in playable and self._is_async(g)]
        self.live_games = [g for g in self.settings.games if g in playable]
        if self.settings.live_games_filter:
            wanted = set(self.settings.live_games_filter)
            self.live_games = [g for g in self.live_games if g in wanted]
        log.info(
            "доступно игр: %d (из них заочно: %d): %s",
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

    def _is_rated(self, game: str) -> bool:
        """Двигает ли эта игра Elo. Кооперативные игры и те, что решаются на
        клиенте, арена помечает как нерейтинговые."""
        meta = self.games_meta.get(game)
        if meta is None:
            return True
        return bool(meta.get("rated"))

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
        """Сказать, на чём мы работаем. Это самоописание, а не удостоверение, но
        именно оно делает вопрос «какая модель лучше играет в шахматы» отвечаемым."""
        try:
            self.client.declare(runtime=self.settings.runtime, model=self.settings.model)
        except (ArenaError, TransportError) as exc:
            log.debug("не удалось объявить runtime/model: %s", exc)

    def _recover_seats(self) -> None:
        """После перезапуска вернуться за столы, где мы всё ещё сидим, вместо
        того чтобы открывать новые."""
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
            log.info("возобновлено столов после перезапуска: %d — %s", len(self.sessions), list(self.sessions))

    def _adopt(
        self, code: str, game: str = "", pace: str = "", mode: str = "ranked", fresh: bool = False
    ) -> MatchSession | None:
        """Привязать сессию к столу, за которым у нас есть место. `fresh`
        означает, что мы только что сели и ещё должны столу приветствие."""
        try:
            payload = self.client.match(code, 0)
        except (ArenaError, TransportError) as exc:
            log.debug("не удалось подхватить %s: %s", code, exc)
            return None
        game = payload.get("game") or game
        if not game:
            return None
        if game not in set(known_games()):
            log.warning("сидим за %s (%s), но мозга под эту игру нет — сдаёмся", code, game)
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
        # Возврат за стол, где мы уже сидели, значит, что мы там уже здоровались;
        # повторять это при каждом перезапуске — шум в чужой комнате.
        session.greeted = not fresh
        self.sessions[code] = session
        return session

    # ----------------------------------------------------------------- линии

    @property
    def live_sessions(self) -> list[MatchSession]:
        return [s for s in self.sessions.values() if s.pace != "async" and not s.finished]

    @property
    def async_sessions(self) -> list[MatchSession]:
        return [s for s in self.sessions.values() if s.pace == "async" and not s.finished]

    def _tables_left(self) -> int:
        return max(0, self.settings.daily_tables - self.client.tables_opened)

    def _budget_ok_for_table(self, lane: str = "async") -> bool:
        """Открытие стола тарифицируется (60 в день на бесплатном тарифе).
        Живая линия способна проесть это в одиночку: стол, к которому никто не
        подсел, бросается через несколько минут и заменяется, — поэтому её
        держит резерв, который вправе тратить только заочные столы."""
        left = self._tables_left()
        if lane == "live":
            return left > self.settings.async_table_reserve
        return left > 2

    def _live_pace_seconds(self) -> float:
        """Сколько ждать между открытиями живых столов, чтобы доля линии в
        дневной квоте растянулась на сутки, а не сгорела за первые часы."""
        spendable = self._tables_left() - self.settings.async_table_reserve
        if spendable <= 0:
            return 3600.0
        now = time.time()
        seconds_to_reset = 86400 - (now % 86400)  # квота обнуляется в полночь UTC
        return max(self.settings.live_wait_seconds, seconds_to_reset / spendable)

    def _next_game(self, pool: list[str], exclude: set[str]) -> str | None:
        """Круг по принципу «давно не садились», чтобы ни одна игра не голодала."""
        options = [g for g in pool if g not in exclude]
        if not options:
            return None
        options.sort(key=lambda g: (self.store.last_seated(g), g))
        return options[0]

    def sweep_turns(self) -> None:
        """Один запрос, покрывающий все столы, за которыми мы сидим.

        `GET /api/my/turns` говорит, где арена ждёт нас, сразу по всем. Именно так
        ведётся заочная линия: опрос семи столов по отдельности потратил бы всю
        дневную квоту пустых чтений на столы, где ничего не произошло, — а заочные
        столы это ровно те, где часами ничего и не происходит."""
        try:
            turns = self.client.my_turns()
        except RateLimited:
            return
        except (ArenaError, TransportError) as exc:
            log.debug("my/turns не отработал: %s", exc)
            return

        due = 0
        for entry in turns.get("turns") or []:
            code = entry.get("code") if isinstance(entry, dict) else entry
            if not code:
                continue
            session = self.sessions.get(code)
            if session is None:
                session = self._adopt(code, entry.get("game", "") if isinstance(entry, dict) else "")
                if session is None:
                    continue
            session.next_poll_at = 0.0
            due += 1
        if due:
            log.debug("my/turns: столов, ждущих нашего хода: %d", due)

    def discover(self) -> None:
        """Одно авторизованное чтение, делающее три дела сразу: держит живыми
        все наши ждущие места, явно не тарифицируется как пустое чтение и
        показывает, к каким столам можно подсесть вместо открытия своего."""
        try:
            tables = self.client.tables()
        except RateLimited:
            return
        except (ArenaError, TransportError) as exc:
            log.debug("не удалось получить список столов: %s", exc)
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
            # Подсадка не притормаживается так, как открытие: там уже кто-то
            # сидит, поэтому партия начинается сразу, а не тратит стол на место,
            # которое может остаться без ответа.
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
            log.debug("не удалось подсесть к %s (%s): %s", code, game, exc.message[:120])
            return False
        except TransportError as exc:
            log.debug("подсадка не удалась: %s", exc)
            return False
        log.info("подсели за %s, стол %s (%s)", game, code, table.get("pace") or "live")
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
        # Стол, из которого вышел настоящий матч, потрачен не зря, поэтому
        # следующий открывается сразу. Паузу запускает только место, которое
        # никто не занял, — именно этот сценарий и жжёт квоту.
        if time.time() < self.live_cooldown_until:
            return

        game = self._next_game(self.live_games, set())
        if not game:
            return

        # Если несколько ранговых столов подряд остались без ответа, значит на
        # арене просто тихо: играем со станционным ботом, а не сидим — так агент
        # продолжает играть, а не греет пустое место. Но матч с ботом Elo не
        # двигает, поэтому в гонке за рейтингом этот откат выключен: там лучше
        # крутить ранговые столы дальше.
        mode = "ranked"
        if self.consecutive_empty_waits >= 2 and not self.settings.rated_only:
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
                # Уже сидим где-то, о чём арена знает, а мы нет.
                code = _code_from_message(exc.message)
                if code and code not in self.sessions:
                    log.info("арена говорит, что мы уже за %s — подхватываем", code)
                    self._adopt(code)
                return
            log.warning("не удалось открыть %s-стол для %s: %s", mode, game, exc.message[:160])
            return
        except TransportError as exc:
            log.warning("не удалось открыть стол: %s", exc)
            return

        table = payload.get("table") if isinstance(payload.get("table"), dict) else payload
        code = table.get("code")
        if not code:
            log.warning("создание стола не вернуло код: %s", str(payload)[:200])
            return
        log.info(
            "открыт %s %s-стол для %s: %s%s",
            pace,
            mode,
            game,
            code,
            f" ({move_hours}ч на ход)" if move_hours else "",
        )
        self.store.note_played(game)
        session = MatchSession(self, code, game, pace=pace, mode=mode)
        session._absorb(table if "status" in table else payload)
        self.sessions[code] = session

    def _reap_stale_waits(self, live: list[MatchSession]) -> None:
        """Живой стол, к которому никто не подсел, — это игра, в которую мы не
        играем. Даём несколько минут и уносим место в другое место."""
        now = time.time()
        for session in live:
            if not session.waiting_for_opponent:
                continue
            if now - session.opened_at < self.settings.live_wait_seconds:
                continue
            log.info(
                "[%s] за %s никто не сел за %.0fс — освобождаем место",
                session.code,
                session.game,
                now - session.opened_at,
            )
            session.resign()
            self.consecutive_empty_waits += 1
            self.live_cooldown_until = now + self._live_pace_seconds()
            self.sessions.pop(session.code, None)

    # ----------------------------------------------------------------- цикл

    def tick(self) -> float:
        """Один проход. Возвращает, сколько вызывающий может поспать."""
        now = time.time()

        for session in list(self.sessions.values()):
            if session.finished:
                self.sessions.pop(session.code, None)
                continue
            if now >= session.next_poll_at:
                try:
                    session.service()
                except Exception:
                    log.exception("[%s] сессия упала, снимаем её", session.code)
                    self.sessions.pop(session.code, None)
                if session.finished:
                    self.sessions.pop(session.code, None)
                    if session.pace != "async":
                        self.consecutive_empty_waits = 0

        if now >= self.next_sweep:
            self.next_sweep = now + self.settings.async_poll_seconds
            self.sweep_turns()

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
        upcoming.append(self.next_sweep)
        return max(0.25, min(min(upcoming) - time.time(), 15.0))

    def _log_status(self) -> None:
        live = self.live_sessions
        async_ = self.async_sessions
        log.info(
            "статус: живых %d (%s), заочных %d (%s) | за сегодня: ходов %d, столов %d, пустых чтений %d из %d",
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
        log.info("работаем — агент будет играть, пока его не остановят")
        while not self.stopping:
            try:
                sleep_for = self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("такт упал, продолжаем")
                sleep_for = 5.0
            time.sleep(max(0.1, sleep_for))

    def shutdown(self, resign_live: bool = False) -> None:
        """Заочные столы оставляем как есть: они по замыслу переживают
        перезапуск, а часы там измеряются часами. Живой стол — тот, что стоит
        сопернику его времени, поэтому по запросу его можно сдать."""
        self.stopping = True
        if not resign_live:
            log.info("оставляем столов на месте: %d — тот же ключ сможет к ним вернуться", len(self.sessions))
            return
        for session in self.live_sessions:
            session.resign()


def _code_from_message(message: str) -> str | None:
    import re

    match = re.search(r"\bmatch ([A-Z0-9]{6,10})\b", message)
    return match.group(1) if match else None

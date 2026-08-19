"""REST-транспорт для Igra Station Arena.

Только стандартная библиотека, и это осознанно: агент должен уметь встать на
любую машину, где есть Python 3, и работать дальше без присмотра.

Сверх обычного HTTP этот слой отвечает за три вещи:

* **Лимиты.** В ответе 429 приходит растущий `Retry-After`; мы соблюдаем его
  глобально, а не для одного вызова, потому что квота считается по ключу.
* **Учёт пустых чтений.** Арена тарифицирует чтения, не принёсшие событий. Мы
  считаем их сами, чтобы runner притормозил раньше, чем это придётся сделать
  арене.
* **Не терять матч из-за сетевого сбоя.** Идемпотентные чтения повторяются, а
  ходы — нет (повторённый ход может быть сыгран дважды), но об уходе, который
  не удалось отправить, сообщается вызывающему, чтобы тот перечитал состояние
  и решил заново.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any

from .config import Settings

log = logging.getLogger("arena.client")


class ArenaError(Exception):
    """Любой ответ арены не из семейства 2xx."""

    def __init__(self, status: int, payload: Any, path: str = ""):
        self.status = status
        self.payload = payload
        self.path = path
        message = payload.get("error") if isinstance(payload, dict) else str(payload)
        super().__init__(f"{status} {path}: {message}")

    @property
    def message(self) -> str:
        if isinstance(self.payload, dict):
            return str(self.payload.get("error", ""))
        return str(self.payload)

    @property
    def quota_exceeded(self) -> bool:
        return "quota_exceeded" in self.message


class RateLimited(ArenaError):
    def __init__(self, status, payload, path, retry_after: float):
        super().__init__(status, payload, path)
        self.retry_after = retry_after


class TransportError(Exception):
    """Запрос вообще не дошёл до арены (DNS, TCP, TLS, таймаут)."""


class ArenaClient:
    """Тонкая и вежливая обёртка над HTTP API арены."""

    def __init__(self, settings: Settings, key: str | None = None):
        self.settings = settings
        self.base = settings.arena_url
        self.key = key
        self._ssl = ssl.create_default_context()
        # Глобальный стоп-кран: до этого момента ни один запрос не уходит.
        self._blocked_until = 0.0
        self._counter_day = date.today()
        self.empty_reads = 0
        self.moves_spent = 0
        self.tables_opened = 0
        self.requests = 0

    # ----------------------------------------------------------------- квота

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._counter_day:
            log.info(
                "дневные счётчики сброшены (было: %d пустых чтений, %d ходов, %d столов)",
                self.empty_reads,
                self.moves_spent,
                self.tables_opened,
            )
            self._counter_day = today
            self.empty_reads = 0
            self.moves_spent = 0
            self.tables_opened = 0

    @property
    def empty_read_headroom(self) -> float:
        """1.0 — квота не тронута, 0.0 — упёрлись в собственный мягкий предел."""
        self._roll_day()
        if self.settings.daily_empty_reads is None:
            return 1.0  # ограничения нет — тормозить незачем
        cap = max(1.0, self.settings.daily_empty_reads * self.settings.empty_read_soft_limit)
        return max(0.0, 1.0 - self.empty_reads / cap)

    def note_empty_read(self) -> None:
        self._roll_day()
        self.empty_reads += 1

    def sync_spend(self, spent: dict) -> None:
        """Принять расход за сутки от самой арены.

        Считать самим недостаточно: арена считает за календарный день, а наш
        счётчик обнуляется при каждом перезапуске процесса. Агент, которого
        перезапускали трижды, считал бы себя свежим и упирался бы в лимит с
        разбегу — а именно это и стоит партий, потому что троттлинг съедает
        время, отведённое на ход.
        """
        if not isinstance(spent, dict):
            return
        self._roll_day()
        for key, attribute in (
            ("read_empty", "empty_reads"),
            ("move", "moves_spent"),
            ("table", "tables_opened"),
        ):
            value = spent.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                # Берём большее: арена — источник истины, но между её ответом и
                # нашим следующим запросом мы уже могли что-то потратить.
                setattr(self, attribute, max(getattr(self, attribute), int(value)))

    @property
    def blocked_for(self) -> float:
        return max(0.0, self._blocked_until - time.time())

    # --------------------------------------------------------------- запрос

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        auth: bool = True,
        timeout: float = 30.0,
        retries: int = 3,
        respect_hold: bool = True,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json"}
        if data is not None:
            headers["content-type"] = "application/json"
        if auth and self.key:
            headers["authorization"] = f"Bearer {self.key}"

        attempt = 0
        while True:
            attempt += 1
            wait = self.blocked_for if respect_hold else 0.0
            if wait > 0:
                log.debug("удержание по лимиту: ждём %.1fс перед %s %s", wait, method, path)
                time.sleep(wait)

            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=self._ssl) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    payload = json.loads(raw) if raw else {}
                except ValueError:
                    payload = {"error": raw.decode("utf-8", "replace")[:400]}

                if exc.code == 429:
                    retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                    self._blocked_until = max(self._blocked_until, time.time() + retry_after)
                    log.warning(
                        "429 на %s — останавливаем весь трафик на %.0fс (%s)",
                        path,
                        retry_after,
                        payload.get("error", ""),
                    )
                    raise RateLimited(exc.code, payload, path, retry_after) from None
                raise ArenaError(exc.code, payload, path) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                if attempt > retries:
                    raise TransportError(f"{method} {path}: {exc}") from None
                backoff = min(16.0, 2.0**attempt)
                log.warning(
                    "ошибка транспорта на %s (%s) — повтор %d/%d через %.0fс",
                    path,
                    exc,
                    attempt,
                    retries,
                    backoff,
                )
                time.sleep(backoff)

    # ------------------------------------------------------------- эндпоинты

    def register(self, agent: str, owner: str, runtime: str = "", model: str = "") -> dict:
        body = {"agent": agent, "owner": owner}
        if runtime:
            body["runtime"] = runtime
        if model:
            body["model"] = model
        out = self._request("POST", "/api/keys", body, auth=False)
        if not out.get("key"):
            raise ArenaError(200, out, "/api/keys")
        return out

    def me(self) -> dict:
        return self._request("GET", "/api/keys/me")

    def declare(self, **fields: str) -> dict:
        """PATCH /api/keys/me — name, owner, runtime, model."""
        body = {k: v for k, v in fields.items() if v}
        return self._request("PATCH", "/api/keys/me", body)

    def games(self) -> list[dict]:
        out = self._request("GET", "/api/games", auth=False)
        return out.get("games", []) if isinstance(out, dict) else out

    def tables(self) -> list[dict]:
        """Открытые столы. Заодно это keep-alive, пока мы ждём соперника:
        арена считает любой авторизованный запрос признаком того, что мы в
        эфире, и именно этот не тарифицирует как пустое чтение."""
        out = self._request("GET", "/api/tables")
        return out.get("tables", []) if isinstance(out, dict) else out

    def create_table(
        self,
        game: str,
        mode: str = "ranked",
        pace: str | None = None,
        move_hours: int | None = None,
        seats: int | None = None,
        options: dict | None = None,
        opponent: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"game": game, "mode": mode}
        if pace:
            body["pace"] = pace
        if move_hours:
            body["move_hours"] = move_hours
        if seats:
            body["seats"] = seats
        if options:
            body["options"] = options
        if opponent:
            body["opponent"] = opponent
        out = self._request("POST", "/api/tables", body)
        self._roll_day()
        self.tables_opened += 1
        return out

    def join_table(self, code: str) -> dict:
        out = self._request("POST", f"/api/tables/{code}/join", {})
        self._roll_day()
        self.tables_opened += 1
        return out

    def my_turns(self) -> dict:
        """Где наш ход — сразу по всем столам, за которыми мы сидим."""
        return self._request("GET", "/api/my/turns")

    def match(self, code: str, since: int = 0) -> dict:
        """Забрать почту: события начиная с `since` плюс свежее полное состояние."""
        return self._request("GET", f"/api/matches/{code}?since={since}")

    def move(self, code: str, move: dict) -> dict:
        """Отправить ход. При сбое транспорта никогда не повторяется: ход,
        который мог дойти, нельзя слать дважды — вызывающий вместо этого
        перечитывает состояние.

        Удержание по 429 на ход не распространяется. Арена тарифицирует только
        пустые чтения и прямо пишет об этом в тексте отказа; ходы она не
        троттлит никогда. Общее удержание, наложенное на отправку хода, — это
        запрет, который мы выписываем себе сами, и он уже стоил выигранной
        партии в шашках: перевес 9:7 и поражение по времени, потому что
        трафик был удержан из-за опроса совсем другого стола.
        """
        out = self._request(
            "POST", f"/api/matches/{code}/move", move, retries=0, respect_hold=False
        )
        self._roll_day()
        self.moves_spent += 1
        return out

    def resign(self, code: str) -> dict:
        # Выход из-за стола — тоже действие, а не чтение: удерживать его нельзя.
        return self._request(
            "POST", f"/api/matches/{code}/resign", {}, respect_hold=False
        )

    def match_page(self, code: str) -> dict:
        return self._request("GET", f"/m/{code}?format=json", auth=False)

    def feed(self) -> list[dict]:
        out = self._request("GET", "/api/feed", auth=False)
        return out.get("feed", []) if isinstance(out, dict) else out

    def leaderboard(self) -> list[dict]:
        out = self._request("GET", "/api/leaderboard", auth=False)
        return out.get("leaderboard", []) if isinstance(out, dict) else out

    def agent_page(self, name: str) -> dict:
        return self._request("GET", f"/a/{name}?format=json", auth=False)


def _retry_after_seconds(header: str | None, default: float = 30.0) -> float:
    if not header:
        return default
    try:
        return max(1.0, float(header))
    except (TypeError, ValueError):
        return default

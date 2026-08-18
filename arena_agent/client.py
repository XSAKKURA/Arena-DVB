"""REST transport for the Igra Station Arena.

Standard library only, on purpose: this agent is meant to be dropped onto any
box with a Python 3 on it and left running.

Three things this layer is responsible for beyond plain HTTP:

* **Rate limits.** A 429 carries a `Retry-After` that grows; we honour it
  globally rather than per call, because the budget is per key.
* **Empty-read accounting.** The arena meters reads that carry no events. We
  count them ourselves so the runner can slow down before the arena has to.
* **Never losing a match to a network blip.** Idempotent reads retry; moves do
  not (a retried move could be played twice), but a move that failed to send
  is reported so the caller can re-read state and decide again.
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
    """Any non-2xx answer from the arena."""

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
    """The request never reached the arena (DNS, TCP, TLS, timeout)."""


class ArenaClient:
    """A thin, well-behaved wrapper over the arena's HTTP API."""

    def __init__(self, settings: Settings, key: str | None = None):
        self.settings = settings
        self.base = settings.arena_url
        self.key = key
        self._ssl = ssl.create_default_context()
        # Global back-pressure: no request goes out before this timestamp.
        self._blocked_until = 0.0
        self._counter_day = date.today()
        self.empty_reads = 0
        self.moves_spent = 0
        self.tables_opened = 0
        self.requests = 0

    # ---------------------------------------------------------------- budget

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._counter_day:
            log.info(
                "daily counters reset (was: %d empty reads, %d moves, %d tables)",
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
        """1.0 = untouched budget, 0.0 = at our own soft limit."""
        self._roll_day()
        cap = max(1.0, self.settings.daily_empty_reads * self.settings.empty_read_soft_limit)
        return max(0.0, 1.0 - self.empty_reads / cap)

    def note_empty_read(self) -> None:
        self._roll_day()
        self.empty_reads += 1

    @property
    def blocked_for(self) -> float:
        return max(0.0, self._blocked_until - time.time())

    # --------------------------------------------------------------- request

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        auth: bool = True,
        timeout: float = 30.0,
        retries: int = 3,
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
            wait = self.blocked_for
            if wait > 0:
                log.debug("rate-limit hold: sleeping %.1fs before %s %s", wait, method, path)
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
                        "429 on %s — holding all traffic for %.0fs (%s)",
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
                    "transport error on %s (%s) — retry %d/%d in %.0fs",
                    path,
                    exc,
                    attempt,
                    retries,
                    backoff,
                )
                time.sleep(backoff)

    # ------------------------------------------------------------- endpoints

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
        """Open tables. Doubles as the keep-alive while we wait for an opponent:
        the arena counts any authenticated request as being on the air, and
        does not meter this one as an empty read."""
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
        """Where it is our move, across every table we sit at."""
        return self._request("GET", "/api/my/turns")

    def match(self, code: str, since: int = 0) -> dict:
        """Drain the mailbox: events since `since`, plus a full fresh state."""
        return self._request("GET", f"/api/matches/{code}?since={since}")

    def move(self, code: str, move: dict) -> dict:
        """Send a move. Never retried on transport failure — a move that may
        have landed must not be sent twice; the caller re-reads state instead."""
        out = self._request("POST", f"/api/matches/{code}/move", move, retries=0)
        self._roll_day()
        self.moves_spent += 1
        return out

    def resign(self, code: str) -> dict:
        return self._request("POST", f"/api/matches/{code}/resign", {})

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

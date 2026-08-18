"""Everything the agent must remember across a restart.

The container this runs in is disposable; the key is not. `state_dir` holds:

* `key.json`      — the arena key. Written 0600, never committed, never logged.
* `journal.jsonl` — one line per finished match, for reading your losses back.
* `stats.json`    — per-game record, used by the live lane to spread its time.
* `opponents.json`— what we have seen a given opponent do, for the games where
                    modelling the other side *is* the game (karateka, pact,
                    threefronts, rps).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any

log = logging.getLogger("arena.store")


def _atomic_write(path: str, text: str, mode: int = 0o644) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, state_dir: str):
        self.dir = os.path.expanduser(state_dir)
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()
        self._stats = self._load_json("stats.json", {})
        self._opponents = self._load_json("opponents.json", {})

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _load_json(self, name: str, default: Any) -> Any:
        try:
            with open(self._path(name), encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return default

    # --------------------------------------------------------- side storage

    def read_json(self, name: str, default: Any = None) -> Any:
        """Read a small JSON file from the state directory, or `default`."""
        return self._load_json(name, default)

    def write_secret(self, name: str, payload: dict) -> None:
        """Write a small JSON file readable only by the owner. For anything
        that would be a credential if it leaked."""
        _atomic_write(self._path(name), json.dumps(payload, indent=2), mode=0o600)

    # ------------------------------------------------------------------ key

    def load_key(self) -> dict | None:
        """The saved key record, or None. Environment wins over the file so a
        deployment can inject the key without touching disk."""
        env_key = os.environ.get("ARENA_KEY")
        if env_key:
            return {"key": env_key, "source": "env"}
        data = self._load_json("key.json", None)
        if data and data.get("key"):
            data["source"] = "file"
            return data
        return None

    def save_key(self, record: dict) -> None:
        payload = dict(record)
        payload.pop("source", None)
        payload["saved_at"] = int(time.time())
        _atomic_write(self._path("key.json"), json.dumps(payload, indent=2), mode=0o600)
        log.info("key saved to %s (name=%s)", self._path("key.json"), payload.get("name"))

    # -------------------------------------------------------------- journal

    def journal(self, entry: dict) -> None:
        entry = {"at": int(time.time()), **entry}
        with self._lock:
            with open(self._path("journal.jsonl"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_journal(self, limit: int = 200) -> list[dict]:
        try:
            with open(self._path("journal.jsonl"), encoding="utf-8") as handle:
                lines = handle.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # ---------------------------------------------------------------- stats

    def record_result(self, game: str, outcome: str) -> None:
        """outcome: win | loss | draw | void | unknown."""
        with self._lock:
            row = self._stats.setdefault(
                game, {"win": 0, "loss": 0, "draw": 0, "void": 0, "unknown": 0, "played": 0}
            )
            row[outcome] = row.get(outcome, 0) + 1
            row["played"] = row.get("played", 0) + 1
            row["last_at"] = int(time.time())
            self._flush_stats()

    def note_played(self, game: str) -> None:
        """Mark that we sat down at a game, whatever came of it. The live lane
        uses this to rotate rather than replaying its favourite."""
        with self._lock:
            row = self._stats.setdefault(
                game, {"win": 0, "loss": 0, "draw": 0, "void": 0, "unknown": 0, "played": 0}
            )
            row["last_seated_at"] = int(time.time())
            self._flush_stats()

    def _flush_stats(self) -> None:
        _atomic_write(self._path("stats.json"), json.dumps(self._stats, indent=2))

    @property
    def stats(self) -> dict:
        return self._stats

    def last_seated(self, game: str) -> float:
        return float(self._stats.get(game, {}).get("last_seated_at", 0))

    # ------------------------------------------------------------ opponents

    def opponent(self, name: str) -> dict:
        return self._opponents.setdefault(name or "?", {})

    def update_opponent(self, name: str, game: str, patch: dict) -> None:
        with self._lock:
            row = self._opponents.setdefault(name or "?", {}).setdefault(game, {})
            for key, value in patch.items():
                if isinstance(value, (int, float)) and isinstance(row.get(key), (int, float)):
                    row[key] += value
                else:
                    row[key] = value
            _atomic_write(self._path("opponents.json"), json.dumps(self._opponents, indent=2))

    def opponent_history(self, name: str, game: str) -> dict:
        return self._opponents.get(name or "?", {}).get(game, {})

"""Всё, что агент обязан помнить между перезапусками.

Контейнер, в котором это работает, одноразовый; ключ — нет. В `state_dir` лежат:

* `key.json`       — ключ арены. Права 0600, никогда не коммитится, не логируется.
* `journal.jsonl`  — по строке на законченный матч, чтобы перечитывать свои поражения.
* `stats.json`     — счёт по играм, по нему живая линия распределяет своё время.
* `opponents.json` — что мы видели у конкретного соперника, для игр, где
                     моделирование другой стороны *и есть* игра (каратека,
                     договор, три фронта, камень-ножницы-бумага).
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

    # ------------------------------------------------------ побочные файлы

    def read_json(self, name: str, default: Any = None) -> Any:
        """Прочитать небольшой JSON из каталога состояния или вернуть `default`."""
        return self._load_json(name, default)

    def write_json(self, name: str, payload: dict) -> None:
        """Записать небольшой JSON в каталог состояния. Для знаний, которые
        должны пережить не только матч, но и перезапуск процесса."""
        _atomic_write(self._path(name), json.dumps(payload, indent=2))

    def write_secret(self, name: str, payload: dict) -> None:
        """Записать небольшой JSON, читаемый только владельцем. Для всего, что
        при утечке стало бы учётными данными."""
        _atomic_write(self._path(name), json.dumps(payload, indent=2), mode=0o600)

    # ------------------------------------------------------------------ ключ

    def load_key(self) -> dict | None:
        """Сохранённая запись ключа или None. Переменная окружения важнее
        файла, чтобы деплой мог подставить ключ, не трогая диск."""
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
        log.info("ключ сохранён в %s (имя=%s)", self._path("key.json"), payload.get("name"))

    # -------------------------------------------------------------- журнал

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

    # ------------------------------------------------------------ статистика

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
        """Отметить, что мы садились за игру, чем бы это ни кончилось. Живая
        линия по этому ротируется, а не переигрывает любимое."""
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

    # ------------------------------------------------------------ соперники

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

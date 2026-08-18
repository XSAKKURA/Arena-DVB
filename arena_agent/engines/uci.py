"""Необязательный мост к UCI-движку (Stockfish и подобные).

Ничто в агенте этого не требует. Если UCI-бинарник случайно есть на машине,
шахматы бесплатно становятся заметно сильнее; если нет, играет встроенный движок.
Указать конкретный можно через `ARENA_UCI_ENGINE=/путь/к/stockfish`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger("arena.uci")

CANDIDATES = ("stockfish", "fairy-stockfish", "lc0")


def find_engine() -> str | None:
    explicit = os.environ.get("ARENA_UCI_ENGINE")
    if explicit and os.path.exists(explicit):
        return explicit
    for name in CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


class UciEngine:
    """Минимальный синхронный драйвер UCI. Один процесс, переиспользуемый между ходами."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.process = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._send("uci")
        self._wait_for("uciok")
        self._send("isready")
        self._wait_for("readyok")
        log.info("UCI-движок готов: %s", path)

    def _send(self, line: str) -> None:
        if not self.process.stdin:
            raise RuntimeError("engine stdin closed")
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def _wait_for(self, token: str, timeout_lines: int = 4000) -> list[str]:
        seen = []
        for _ in range(timeout_lines):
            if not self.process.stdout:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            seen.append(line.strip())
            if line.startswith(token):
                return seen
        raise RuntimeError(f"UCI engine did not answer with {token}")

    def best_move(self, fen: str, seconds: float) -> str | None:
        """Возвращает ход в длинной алгебраической записи, например 'e2e4' или 'a7a8q'."""
        with self._lock:
            try:
                self._send("ucinewgame")
                self._send(f"position fen {fen}")
                self._send(f"go movetime {int(max(50, seconds * 1000))}")
                for line in self._wait_for("bestmove"):
                    if line.startswith("bestmove"):
                        parts = line.split()
                        if len(parts) > 1 and parts[1] not in ("(none)", "0000"):
                            return parts[1]
            except (RuntimeError, OSError, BrokenPipeError) as exc:
                log.warning("UCI-движок отказал (%s) — откатываемся к встроенному поиску", exc)
                self.close()
            return None

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self._send("quit")
                self.process.wait(timeout=3)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass


_ENGINE: UciEngine | None = None
_TRIED = False


def shared_engine() -> UciEngine | None:
    """Один процесс движка на весь агент, запускается при первом обращении."""
    global _ENGINE, _TRIED
    if _ENGINE is not None:
        return _ENGINE if _ENGINE.process.poll() is None else None
    if _TRIED:
        return None
    _TRIED = True
    path = find_engine()
    if not path:
        log.info("UCI-движок не найден — используем встроенный шахматный поиск")
        return None
    try:
        _ENGINE = UciEngine(path)
    except Exception as exc:
        log.warning("не удалось запустить UCI-движок %s: %s", path, exc)
        _ENGINE = None
    return _ENGINE

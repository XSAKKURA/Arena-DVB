"""Static facts about the arena and the knobs that tune this agent.

Anything the arena itself decides (deadlines, budgets, rules versions) is read
from the API at runtime; the numbers here are only fallbacks for the first call
and defaults for our own scheduling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ARENA_URL = os.environ.get("ARENA_URL", "https://arena.roomcomm.xyz").rstrip("/")
ROOMCOMM_URL = os.environ.get("ROOMCOMM_URL", "https://roomcomm.xyz").rstrip("/")

STATE_DIR = os.path.expanduser(os.environ.get("ARENA_STATE_DIR", "~/.arena-dvb"))

DEFAULT_AGENT_NAME = os.environ.get("ARENA_AGENT_NAME", "DVB-Arena")
DEFAULT_OWNER = os.environ.get("ARENA_OWNER", "xsakkura")
DEFAULT_RUNTIME = os.environ.get("ARENA_RUNTIME", "Claude Code")
DEFAULT_MODEL = os.environ.get("ARENA_MODEL", "Opus 5")

# Every game the arena opens to agents. Order is the round-robin order of the
# live lane, so cheap fast games sit next to long ones and no game starves.
ALL_GAMES = [
    "gomoku",
    "reversi",
    "bulls",
    "chess",
    "checkers",
    "dotsboxes",
    "seabattle",
    "tanks",
    "karateka",
    "rps",
    "rpsls",
    "threefronts",
    "pact",
    "rule",
    "onewave",
    "artillery",
    "durak",
    "president",
    "believe",
    "mind",
    "fifteen",
]

# Games that can be played by correspondence (`pace:"async"`). Confirmed
# against GET /api/games; the runner re-checks at startup and adapts.
ASYNC_GAMES = [
    "gomoku",
    "reversi",
    "bulls",
    "chess",
    "checkers",
    "dotsboxes",
    "seabattle",
    "tanks",
]

# Games with a station bot, so `mode:"practice"` actually has an opponent.
PRACTICE_BOT_GAMES = ["karateka", "rps", "rpsls", "bulls", "seabattle"]

# One seat, starts immediately, no waiting for anybody.
SOLO_GAMES = ["fifteen"]

# Games that need more than two seats before they start. Opening these as a
# ranked table means waiting for two or three strangers at once, which almost
# never fills — the live lane deprioritises them.
MIN_SEATS = {"president": 3, "mind": 2, "believe": 2}


@dataclass
class Settings:
    """Everything the runner can be told to do differently."""

    arena_url: str = ARENA_URL
    roomcomm_url: str = ROOMCOMM_URL
    state_dir: str = STATE_DIR

    agent_name: str = DEFAULT_AGENT_NAME
    owner: str = DEFAULT_OWNER
    runtime: str = DEFAULT_RUNTIME
    model: str = DEFAULT_MODEL

    # --- lanes -------------------------------------------------------------
    # "One key, one live table" is an arena rule, not a preference.
    max_live_tables: int = 1
    # The arena allows 8 correspondence tables. Leave one slot of headroom so a
    # table we think is closed but is not cannot lock the lane out.
    max_async_tables: int = 7
    enable_async_lane: bool = True
    enable_live_lane: bool = True

    # Which games each lane is allowed to open.
    games: list[str] = field(default_factory=lambda: list(ALL_GAMES))

    # --- polling cadence ---------------------------------------------------
    # Reads that carry events are never metered. These are the empty-read
    # cadences, and they are what the daily budget is spent on.
    live_poll_min_seconds: float = 2.0
    live_poll_max_seconds: float = 20.0
    async_poll_seconds: float = 150.0
    # While a table waits for an opponent we poll GET /api/tables instead: it
    # keeps the seat alive and is explicitly not metered as an empty read.
    waiting_poll_seconds: float = 25.0

    # --- deadlines ---------------------------------------------------------
    # Fallbacks only; the real numbers come from the table payload.
    default_move_deadline_seconds: int = 900
    human_move_deadline_seconds: int = 90
    # Move this far before the deadline rather than flirting with the reserve.
    deadline_safety_margin: float = 0.5

    # How long to sit at an unfilled ranked table before giving up on it and
    # trying a different game. The arena closes it at 10 minutes anyway.
    live_wait_seconds: float = 240.0
    # Correspondence tables wait 7 days for an opponent at no cost to us, so
    # there is no reason to reap them early.
    async_move_hours: int = 24

    # --- budget ------------------------------------------------------------
    # Stop spending empty reads at this fraction of the daily allowance, so a
    # real match always has budget left to be played out.
    empty_read_soft_limit: float = 0.75
    daily_empty_reads: int = 1500
    daily_moves: int = 3000
    daily_tables: int = 60

    # --- thinking time -----------------------------------------------------
    # Wall-clock a search brain may spend on one move. Live matches stay snappy
    # out of politeness; correspondence can afford to think.
    live_think_seconds: float = 3.0
    async_think_seconds: float = 8.0

    # --- chat --------------------------------------------------------------
    enable_chat: bool = True

    # --- misc --------------------------------------------------------------
    practice_when_idle: bool = True
    log_level: str = os.environ.get("ARENA_LOG_LEVEL", "INFO")
    dry_run: bool = False

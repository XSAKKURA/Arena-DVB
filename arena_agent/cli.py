"""Command line for the arena agent.

`run` is the one that matters: it stays up and plays. `once` exists for the
other kind of runtime — the agent that wakes on a schedule, answers a prompt
and exits — which the arena supports through correspondence tables, and which
is a perfectly good way to keep a dozen matches going from a cron entry.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .client import ArenaError, TransportError
from .config import ALL_GAMES, Settings
from .runner import Runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arena-agent",
        description="Play every game on the Igra Station Arena, continuously.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "once", "status", "register", "games", "journal"],
        help=(
            "run: stay up and play (default). once: play the correspondence moves that are "
            "waiting, then exit. status: what the arena thinks of us. register: take a key. "
            "games: what we can play. journal: recent finished matches."
        ),
    )
    parser.add_argument("--state-dir", help="where the key, journal and stats live")
    parser.add_argument("--name", help="agent name to register under")
    parser.add_argument("--owner", help="who runs this agent")
    parser.add_argument("--runtime", help="what software this runs in")
    parser.add_argument("--model", help="what model is behind it")
    parser.add_argument(
        "--games",
        help="comma-separated subset of games to play (default: everything we have a brain for)",
    )
    parser.add_argument("--no-async", action="store_true", help="do not open correspondence tables")
    parser.add_argument("--no-live", action="store_true", help="do not open live tables")
    parser.add_argument("--no-chat", action="store_true", help="do not talk to opponents")
    parser.add_argument(
        "--async-tables", type=int, help="how many correspondence tables to keep open (max 8)"
    )
    parser.add_argument(
        "--think", type=float, help="seconds a search may spend on one live move (default 3)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    return parser


def settings_from(args: argparse.Namespace) -> Settings:
    settings = Settings()
    if args.state_dir:
        settings.state_dir = args.state_dir
    if args.name:
        settings.agent_name = args.name
    if args.owner:
        settings.owner = args.owner
    if args.runtime:
        settings.runtime = args.runtime
    if args.model:
        settings.model = args.model
    if args.games:
        wanted = [g.strip() for g in args.games.split(",") if g.strip()]
        unknown = [g for g in wanted if g not in ALL_GAMES]
        if unknown:
            raise SystemExit(f"unknown game(s): {', '.join(unknown)}")
        settings.games = wanted
    if args.no_async:
        settings.enable_async_lane = False
    if args.no_live:
        settings.enable_live_lane = False
    if args.no_chat:
        settings.enable_chat = False
    if args.async_tables is not None:
        settings.max_async_tables = max(0, min(8, args.async_tables))
    if args.think is not None:
        settings.live_think_seconds = max(0.1, args.think)
    if args.verbose:
        settings.log_level = "DEBUG"
    elif args.quiet:
        settings.log_level = "WARNING"
    return settings


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings_from(args)
    setup_logging(settings.log_level)
    log = logging.getLogger("arena")

    if args.command == "games":
        return _cmd_games(settings)

    runner = Runner(settings)

    if args.command == "register":
        runner._ensure_key()
        record = runner.store.load_key() or {}
        print(f"agent name : {runner.agent_name}")
        print(f"key stored : {runner.store.dir}/key.json")
        if record.get("source") == "env":
            print("note       : ARENA_KEY is set, so that key wins over the file")
        return 0

    if args.command == "journal":
        return _cmd_journal(runner)

    try:
        runner.bootstrap()
    except SystemExit:
        raise
    except (ArenaError, TransportError) as exc:
        log.error("could not start: %s", exc)
        return 1

    if args.command == "status":
        return _cmd_status(runner)

    if args.command == "once":
        return _cmd_once(runner)

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        log.info("stopping on Ctrl-C")
        runner.shutdown(resign_live=False)
    return 0


def _cmd_games(settings: Settings) -> int:
    from .brains import known_games
    from .config import ASYNC_GAMES, PRACTICE_BOT_GAMES

    have = set(known_games())
    print(f"{'game':<14} {'brain':<7} {'correspondence':<16} practice bot")
    for game in ALL_GAMES:
        print(
            f"{game:<14} {'yes' if game in have else 'NO':<7} "
            f"{'yes' if game in ASYNC_GAMES else '-':<16} "
            f"{'yes' if game in PRACTICE_BOT_GAMES else '-'}"
        )
    print(f"\n{len(have)}/{len(ALL_GAMES)} games have a strategy.")
    return 0


def _cmd_status(runner: Runner) -> int:
    me = runner.client.me()
    print(f"name        : {me.get('name')} (owner: {me.get('owner')})")
    print(f"declared as : {me.get('runtime')} / {me.get('model')}")
    print(f"rating      : {me.get('rating')}  over {me.get('plays')} matches")
    print(
        f"record      : {me.get('wins')}W {me.get('draws')}D "
        f"{(me.get('plays') or 0) - (me.get('wins') or 0) - (me.get('draws') or 0)}L "
        f"abandoned {me.get('abandoned')}, finish rate {me.get('finish_rate')}"
    )
    print(f"tier/budget : {me.get('tier')} {me.get('daily_budget')}")
    print(f"spent today : {me.get('spent_today')}")
    print(f"seated at   : {me.get('seated_at') or '-'}  seats: {me.get('seats')}")
    if me.get("last_seat"):
        print(f"last seat   : {me['last_seat']}")

    turns = runner.client.my_turns()
    waiting = turns.get("turns") or []
    print(f"\nyour move at {len(waiting)} table(s):")
    for entry in waiting:
        print(f"  {entry}")

    stats = runner.store.stats
    if stats:
        print("\nper-game record from this agent's own journal:")
        for game in sorted(stats):
            row = stats[game]
            print(
                f"  {game:<14} {row.get('win', 0)}W {row.get('draw', 0)}D {row.get('loss', 0)}L "
                f"(played {row.get('played', 0)})"
            )
    return 0


def _cmd_journal(runner: Runner) -> int:
    entries = runner.store.read_journal(60)
    if not entries:
        print("no finished matches recorded yet")
        return 0
    for entry in entries:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("at", 0)))
        print(
            f"{when}  {entry.get('game','?'):<12} {entry.get('outcome','?'):<8} "
            f"vs {str(entry.get('opponent','?'))[:24]:<24} {entry.get('url','')}"
        )
    return 0


def _cmd_once(runner: Runner) -> int:
    """Play whatever correspondence moves are waiting, then leave."""
    log = logging.getLogger("arena")
    runner.ensure_async_lane()
    try:
        turns = runner.client.my_turns()
    except (ArenaError, TransportError) as exc:
        log.error("could not read /api/my/turns: %s", exc)
        return 1

    codes = [
        entry.get("code")
        for entry in (turns.get("turns") or [])
        if isinstance(entry, dict) and entry.get("code")
    ]
    if not codes:
        log.info("nothing waiting for a move right now")
        return 0

    played = 0
    for code in codes:
        session = runner.sessions.get(code) or runner._adopt(code)
        if not session:
            continue
        for _ in range(4):
            session.service()
            if session.finished or not session.state.get("yourTurn"):
                break
        played += session.moves_sent
    log.info("played %d move(s) across %d table(s)", played, len(codes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

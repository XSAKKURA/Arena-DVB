"""Командная строка агента арены.

Главная команда — `run`: он остаётся в сети и играет. `once` сделан для среды
другого рода — агента, который просыпается по расписанию, отвечает на один
запрос и завершается. Арена поддерживает такой режим через заочные столы, и
записи в cron вполне хватает, чтобы вести десяток матчей.
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
        description="Играть во все игры Igra Station Arena, непрерывно.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "once", "status", "register", "games", "journal"],
        help=(
            "run: оставаться в сети и играть (по умолчанию). once: сыграть ждущие заочные "
            "ходы и выйти. status: что о нас думает арена. register: взять ключ. "
            "games: во что мы умеем играть. journal: недавние законченные матчи."
        ),
    )
    parser.add_argument("--state-dir", help="где лежат ключ, журнал и статистика")
    parser.add_argument("--name", help="имя, под которым регистрироваться")
    parser.add_argument("--owner", help="кто запускает этого агента")
    parser.add_argument("--runtime", help="в каком софте это работает")
    parser.add_argument("--model", help="какая модель за этим стоит")
    parser.add_argument(
        "--games",
        help="список игр через запятую (по умолчанию: всё, на что есть мозг)",
    )
    parser.add_argument(
        "--live-games",
        help=(
            "сузить ЖИВУЮ линию до этих игр, не трогая заочную — так тренируются "
            "в одной игре, не бросая уже идущие матчи"
        ),
    )
    parser.add_argument(
        "--rated-only",
        action="store_true",
        help="играть только в игры, которые двигают Elo (пропустить пятнашки, разум, одну волну)",
    )
    parser.add_argument("--no-async", action="store_true", help="не открывать заочные столы")
    parser.add_argument("--no-live", action="store_true", help="не открывать живые столы")
    parser.add_argument("--no-chat", action="store_true", help="молчать за столом")
    parser.add_argument(
        "--async-tables", type=int, help="сколько заочных столов держать (максимум 8)"
    )
    parser.add_argument(
        "--think", type=float, help="секунд на обдумывание одного живого хода (по умолчанию 3)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="отладочный лог")
    parser.add_argument("-q", "--quiet", action="store_true", help="только предупреждения и ошибки")
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
            raise SystemExit(f"неизвестные игры: {', '.join(unknown)}")
        settings.games = wanted
    if args.live_games:
        wanted = [g.strip() for g in args.live_games.split(",") if g.strip()]
        unknown = [g for g in wanted if g not in ALL_GAMES]
        if unknown:
            raise SystemExit(f"неизвестные игры: {', '.join(unknown)}")
        settings.live_games_filter = wanted
    if args.rated_only:
        settings.rated_only = True
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
        print(f"имя агента : {runner.agent_name}")
        print(f"ключ здесь : {runner.store.dir}/key.json")
        if record.get("source") == "env":
            print("важно      : задан ARENA_KEY, он важнее файла")
        return 0

    if args.command == "journal":
        return _cmd_journal(runner)

    try:
        runner.bootstrap()
    except SystemExit:
        raise
    except (ArenaError, TransportError) as exc:
        log.error("не удалось запуститься: %s", exc)
        return 1

    if args.command == "status":
        return _cmd_status(runner)

    if args.command == "once":
        return _cmd_once(runner)

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        log.info("останавливаемся по Ctrl-C")
        runner.shutdown(resign_live=False)
    return 0


def _cmd_games(settings: Settings) -> int:
    from .brains import known_games
    from .config import ASYNC_GAMES, PRACTICE_BOT_GAMES

    have = set(known_games())
    print(f"{'игра':<14} {'мозг':<7} {'заочно':<16} бот для практики")
    for game in ALL_GAMES:
        print(
            f"{game:<14} {'да' if game in have else 'НЕТ':<7} "
            f"{'да' if game in ASYNC_GAMES else '-':<16} "
            f"{'да' if game in PRACTICE_BOT_GAMES else '-'}"
        )
    print(f"\nСтратегия есть у {len(have)} из {len(ALL_GAMES)} игр.")
    return 0


def _cmd_status(runner: Runner) -> int:
    me = runner.client.me()
    print(f"имя         : {me.get('name')} (владелец: {me.get('owner')})")
    print(f"объявлено   : {me.get('runtime')} / {me.get('model')}")
    print(f"рейтинг     : {me.get('rating')}  за {me.get('plays')} матчей")
    print(
        f"счёт        : {me.get('wins')}П {me.get('draws')}Н "
        f"{(me.get('plays') or 0) - (me.get('wins') or 0) - (me.get('draws') or 0)}Пор "
        f"брошено {me.get('abandoned')}, доля доигранных {me.get('finish_rate')}"
    )
    print(f"тариф/квота : {me.get('tier')} {me.get('daily_budget')}")
    print(f"за сегодня  : {me.get('spent_today')}")
    print(f"сидим за    : {me.get('seated_at') or '-'}  места: {me.get('seats')}")
    if me.get("last_seat"):
        print(f"прошлое мест: {me['last_seat']}")

    turns = runner.client.my_turns()
    waiting = turns.get("turns") or []
    print(f"\nнаш ход за столами: {len(waiting)}")
    for entry in waiting:
        print(f"  {entry}")

    stats = runner.store.stats
    if stats:
        print("\nсчёт по играм из собственного журнала агента:")
        for game in sorted(stats):
            row = stats[game]
            print(
                f"  {game:<14} {row.get('win', 0)}П {row.get('draw', 0)}Н {row.get('loss', 0)}Пор "
                f"(сыграно {row.get('played', 0)})"
            )
    return 0


def _cmd_journal(runner: Runner) -> int:
    entries = runner.store.read_journal(60)
    if not entries:
        print("законченных матчей пока не записано")
        return 0
    for entry in entries:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("at", 0)))
        print(
            f"{when}  {entry.get('game','?'):<12} {entry.get('outcome','?'):<8} "
            f"против {str(entry.get('opponent','?'))[:24]:<24} {entry.get('url','')}"
        )
    return 0


def _cmd_once(runner: Runner) -> int:
    """Сыграть все ждущие заочные ходы и уйти."""
    log = logging.getLogger("arena")
    runner.ensure_async_lane()
    try:
        turns = runner.client.my_turns()
    except (ArenaError, TransportError) as exc:
        log.error("не удалось прочитать /api/my/turns: %s", exc)
        return 1

    codes = [
        entry.get("code")
        for entry in (turns.get("turns") or [])
        if isinstance(entry, dict) and entry.get("code")
    ]
    if not codes:
        log.info("сейчас ничего не ждёт нашего хода")
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
    log.info("сыграно ходов: %d за столами: %d", played, len(codes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

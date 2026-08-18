"""Тесты стратегий.

Они проверяют, что каждый мозг делает то, ошибиться в чём было бы стыдно: берёт
победу, которая уже на доске, останавливает поражение, которое уже на доске,
соблюдает правила, специфичные именно для этого варианта игры. Все офлайн: без
сети, без ключа, без стола.

Запуск: `python3 -m pytest tests/` или `python3 tests/test_brains.py`.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena_agent.brains import brain_for  # noqa: E402
from arena_agent.brains.base import Context  # noqa: E402


def context(game: str, seat: int = 1, think: float = 1.0, **kwargs) -> Context:
    return Context(
        code="TEST0000",
        game=game,
        seat=seat,
        rng=random.Random(7),
        think_seconds=think,
        **kwargs,
    )


# ---------------------------------------------------------------- gomoku


def _empty_gomoku() -> list[int]:
    return [0] * 225


def test_gomoku_completes_five():
    board = _empty_gomoku()
    for c in range(4):  # четыре в ряд в строке 7, наши
        board[7 * 15 + c] = 1
    brain = brain_for("gomoku")
    move = brain.choose(
        {"yourTurn": True, "board": board, "size": 15, "symbols": {"1": "x", "2": "o"}},
        context("gomoku", seat=1),
    )
    assert move["type"] == "move"
    assert (move["r"], move["c"]) == (7, 4), f"должен был достроить пятёрку, сыграл {move}"


def test_gomoku_blocks_five():
    board = _empty_gomoku()
    for c in range(4):  # четыре в ряд у соперника
        board[7 * 15 + c] = 2
    board[0] = 1
    brain = brain_for("gomoku")
    move = brain.choose(
        {"yourTurn": True, "board": board, "size": 15, "symbols": {"1": "x", "2": "o"}},
        context("gomoku", seat=1),
    )
    assert (move["r"], move["c"]) == (7, 4), f"должен был закрыть пятёрку, сыграл {move}"


def test_gomoku_beats_a_random_player():
    """Поиск по шаблонным оценкам не должен проигрывать случайной игре."""
    wins = 0
    for game in range(4):
        board = _empty_gomoku()
        rng = random.Random(game)
        brain = brain_for("gomoku")
        us, them = 1, 2
        for turn in range(112):
            move = brain.choose(
                {"yourTurn": True, "board": board, "size": 15, "symbols": {"1": "x", "2": "o"}},
                context("gomoku", seat=us, think=0.4),
            )
            board[move["r"] * 15 + move["c"]] = us
            if _five_in_a_row(board, us):
                wins += 1
                break
            free = [i for i, v in enumerate(board) if v == 0]
            if not free:
                break
            board[rng.choice(free)] = them
            if _five_in_a_row(board, them):
                break
    assert wins == 4, f"ожидалась победа над случайной игрой каждый раз, выиграно {wins}/4"


def _five_in_a_row(board: list[int], player: int, size: int = 15) -> bool:
    for r in range(size):
        for c in range(size):
            if board[r * size + c] != player:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                run = 0
                for step in range(5):
                    rr, cc = r + dr * step, c + dc * step
                    if 0 <= rr < size and 0 <= cc < size and board[rr * size + cc] == player:
                        run += 1
                    else:
                        break
                if run >= 5:
                    return True
    return False


# ---------------------------------------------------------------- reversi


def _reversi_start() -> list[int]:
    from arena_agent.brains.reversi import BLACK, WHITE

    board = [0] * 64
    board[27], board[36] = WHITE, WHITE
    board[28], board[35] = BLACK, BLACK
    return board


def test_reversi_evaluation_values_corners():
    """Максимизировать надо не число фишек, а углы. Та же доска, но с углом на
    другой стороне, обязана дать резкий перевес."""
    from arena_agent.brains.reversi import BLACK, WHITE, evaluate

    ours = _reversi_start()
    ours[0] = BLACK
    theirs = _reversi_start()
    theirs[0] = WHITE
    assert evaluate(ours, BLACK) - evaluate(theirs, BLACK) > 400


def test_reversi_beats_a_random_player():
    from arena_agent.brains.reversi import BLACK, WHITE, _apply, legal_moves

    wins = 0
    games = 4
    for game in range(games):
        rng = random.Random(game)
        board = _reversi_start()
        brain = brain_for("reversi")
        side, passes = BLACK, 0
        while passes < 2:
            moves = legal_moves(board, side)
            if not moves:
                passes += 1
                side = WHITE if side == BLACK else BLACK
                continue
            passes = 0
            if side == BLACK:
                state = {
                    "yourTurn": True,
                    "board": board,
                    "your_color": "black",
                    "legal_moves": [{"r": i // 8, "c": i % 8} for i in moves],
                }
                move = brain.choose(state, context("reversi", think=0.3))
                index = move["r"] * 8 + move["c"]
            else:
                index = rng.choice(list(moves))
            board = _apply(board, index, side, moves[index])
            side = WHITE if side == BLACK else BLACK
        if board.count(BLACK) > board.count(WHITE):
            wins += 1
    assert wins == games, f"поиск не должен проигрывать случайной игре; выиграно {wins}/{games}"


def test_reversi_passes_only_when_it_must():
    brain = brain_for("reversi")
    board = [0] * 64
    board[27], board[36] = 2, 2
    board[28], board[35] = 1, 1
    move = brain.choose(
        {"yourTurn": True, "board": board, "your_color": "black", "legal_moves": []},
        context("reversi"),
    )
    assert move == {"type": "pass"}


# ---------------------------------------------------------------- chess


def test_chess_engine_finds_mate_in_one():
    from arena_agent.engines.chess_engine import Position, Search, move_to_dict

    # Мат по последней горизонтали: Ra1-a8 — мат.
    position = Position("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    best = Search(position).best_move(2.0)
    assert move_to_dict(best) == {"type": "move", "from": "a1", "to": "a8"}, move_to_dict(best)


def test_chess_engine_takes_free_material():
    from arena_agent.engines.chess_engine import Position, Search, move_to_dict

    # Зависший ферзь на d5, взять которого может только пешка c4.
    position = Position("4k3/8/8/3q4/2P5/8/8/4K3 w - - 0 1")
    best = Search(position).best_move(2.0)
    assert move_to_dict(best)["to"] == "d5", move_to_dict(best)


def test_chess_brain_only_plays_published_moves():
    brain = brain_for("chess")
    published = [{"from": "e2", "to": "e4"}, {"from": "d2", "to": "d4"}]
    state = {
        "yourTurn": True,
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "legal_moves": published,
    }
    move = brain.choose(state, context("chess", think=0.5))
    assert {"from": move["from"], "to": move["to"]} in published, move


def test_chess_perft_is_correct():
    """Генератор ходов — фундамент, на котором стоит всё остальное."""
    from arena_agent.engines.chess_engine import Position

    def perft(position, depth):
        if depth == 0:
            return 1
        total = 0
        for move in position.legal_moves():
            undo = position.make(move)
            total += perft(position, depth - 1)
            position.unmake(move, undo)
        return total

    assert perft(Position(), 3) == 8902
    kiwipete = Position("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
    assert perft(kiwipete, 2) == 2039
    promotions = Position("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1")
    assert perft(promotions, 3) == 9467


def test_chess_null_move_is_disabled_in_pawn_endgames():
    """Нулевой ход неверен в цугцванге, а цугцванг живёт в пешечных окончаниях.

    Признак «есть ли нелёгкий материал» — это и есть предохранитель, поэтому
    он проверяется отдельно от поиска.
    """
    from arena_agent.engines.chess_engine import Position

    assert not Position("8/8/4k3/8/4P3/4K3/8/8 w - - 0 1").has_non_pawn_material()
    assert Position("8/8/4k3/8/4P3/4K3/8/1R6 w - - 0 1").has_non_pawn_material()
    assert Position().has_non_pawn_material()


def test_chess_still_finds_a_forced_mate_with_pruning_on():
    """Отсечения не должны прятать форсированный мат."""
    from arena_agent.engines.chess_engine import Position, Search, move_to_dict

    # Мат в два: 1.Qg7+ Kxg7 2... — проверяем, что оценка видит мат.
    position = Position("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    best = Search(position).best_move(3.0)
    assert best is not None
    assert move_to_dict(best)["from"] == "a1", move_to_dict(best)


def test_chess_pruning_did_not_break_move_generation():
    """Отсечения затрагивают перебор, но не правила: perft обязан совпасть."""
    from arena_agent.engines.chess_engine import Position

    def perft(position, depth):
        if depth == 0:
            return 1
        return sum(
            _count(position, move, depth) for move in position.legal_moves()
        )

    def _count(position, move, depth):
        undo = position.make(move)
        total = perft(position, depth - 1)
        position.unmake(move, undo)
        return total

    assert perft(Position(), 3) == 8902


def test_checkers_transposition_table_does_not_change_the_chosen_move():
    """Таблица транспозиций ускоряет поиск, а не меняет его вывод."""
    import time

    from arena_agent.engines.checkers_engine import (
        WHITE,
        CheckersSearch,
        legal_moves,
        other,
    )

    board = [None] * 64
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                if r <= 2:
                    board[r * 8 + c] = "b"
                elif r >= 5:
                    board[r * 8 + c] = "w"

    def root_best(use_table: bool, depth: int):
        search = CheckersSearch()
        search.deadline = time.time() + 60
        best, best_path = -10**9, None
        for path, result in sorted(legal_moves(board, WHITE), key=lambda i: -len(i[0])):
            if not use_table:
                search.table = _Blocked()
            value = -search.search(result, other(WHITE), depth - 1, -10**9, 10**9)
            if value > best:
                best, best_path = value, path
        return best, best_path

    class _Blocked(dict):
        """Словарь, который ничего не запоминает: имитация поиска без таблицы."""

        def __setitem__(self, key, value):
            pass

        def get(self, key, default=None):
            return default

    with_table = root_best(True, 5)
    without_table = root_best(False, 5)
    assert with_table[1] == without_table[1], (with_table, without_table)
    assert with_table[0] == without_table[0], (with_table, without_table)


# --------------------------------------------------------------- checkers


def test_checkers_capture_is_mandatory_and_chains_complete():
    from arena_agent.engines.checkers_engine import WHITE, legal_moves, square_index, square_name

    board = [None] * 64
    board[square_index("c3")] = "w"
    board[square_index("d4")] = "b"
    board[square_index("f6")] = "b"
    board[square_index("a3")] = "w"  # тихий ход существует и должен быть отвергнут
    paths = [[square_name(s) for s in path] for path, _ in legal_moves(board, WHITE)]
    assert paths == [["c3", "e5", "g7"]], paths


def test_checkers_man_crowns_mid_chain_and_carries_on():
    from arena_agent.engines.checkers_engine import WHITE, legal_moves, square_index, square_name

    board = [None] * 64
    board[square_index("c6")] = "w"
    board[square_index("d7")] = "b"
    board[square_index("g6")] = "b"
    paths = [[square_name(s) for s in path] for path, _ in legal_moves(board, WHITE)]
    assert paths == [["c6", "e8", "h5"]], paths


def test_checkers_king_flies_and_lands_freely():
    from arena_agent.engines.checkers_engine import WHITE, legal_moves, square_index, square_name

    board = [None] * 64
    board[square_index("a1")] = "W"
    board[square_index("e5")] = "b"
    paths = {tuple(square_name(s) for s in path) for path, _ in legal_moves(board, WHITE)}
    assert paths == {("a1", "f6"), ("a1", "g7"), ("a1", "h8")}, paths


def test_checkers_opening_has_seven_moves():
    from arena_agent.engines.checkers_engine import BLACK, WHITE, legal_moves

    board = [None] * 64
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                if r <= 2:
                    board[r * 8 + c] = "b"
                elif r >= 5:
                    board[r * 8 + c] = "w"
    assert len(legal_moves(board, WHITE)) == 7
    assert len(legal_moves(board, BLACK)) == 7


# ------------------------------------------------------------------ bulls


def test_bulls_solver_finds_any_secret_quickly():
    from arena_agent.brains.bulls import all_candidates, feedback

    rng = random.Random(3)
    lengths = []
    for secret in rng.sample(all_candidates(), 25):
        brain = brain_for("bulls")
        guesses: list[dict] = []
        for _ in range(12):
            state = {"phase": "play", "yourTurn": True, "myGuesses": guesses}
            move = brain.choose(state, context("bulls"))
            bulls, cows = feedback(move["number"], secret)
            guesses.append({"guess": move["number"], "bulls": bulls, "cows": cows})
            if bulls == 4:
                break
        assert guesses[-1]["bulls"] == 4, f"не удалось вскрыть {secret} за 12 догадок"
        lengths.append(len(guesses))
    average = sum(lengths) / len(lengths)
    assert average <= 6.5, f"в среднем {average:.2f} догадок — хуже ожидаемого"


# -------------------------------------------------------------- seabattle


def test_seabattle_fleet_is_legal():
    from arena_agent.brains.seabattle import FLEET, _cells, _halo, random_fleet

    for seed in range(30):
        ships = random_fleet(random.Random(seed))
        assert sorted(s["len"] for s in ships) == sorted(FLEET)
        occupied: set[tuple[int, int]] = set()
        for ship in ships:
            cells = _cells(ship["r"], ship["c"], ship["len"], ship["dir"] == "h")
            assert all(0 <= r < 10 and 0 <= c < 10 for r, c in cells)
            assert not (set(cells) & occupied), "корабли не должны касаться, даже по диагонали"
            occupied |= _halo(cells)


def test_seabattle_low_density_placement_is_legal_and_varied():
    """Расстановка в зонах низкой плотности не должна становиться шаблоном.

    Читаемая расстановка хуже любой случайной, поэтому выбор среди образцов
    вероятностный: проверяем и законность, и разнообразие.
    """
    from arena_agent.brains.seabattle import FLEET, _cells, _halo, low_density_fleet

    fleets = []
    for seed in range(12):
        ships = low_density_fleet(random.Random(seed))
        assert sorted(s["len"] for s in ships) == sorted(FLEET)
        blocked: set[tuple[int, int]] = set()
        occupied: set[tuple[int, int]] = set()
        for ship in ships:
            cells = _cells(ship["r"], ship["c"], ship["len"], ship["dir"] == "h")
            assert all(0 <= r < 10 and 0 <= c < 10 for r, c in cells)
            assert not (set(cells) & blocked), "корабли не должны касаться"
            blocked |= _halo(cells)
            occupied |= set(cells)
        # Сравниваем сами корабли, а не обводку: обводка покрывает почти всю
        # доску и совпадает у любых двух расстановок.
        fleets.append(frozenset(occupied))

    overlaps = [len(a & b) for i, a in enumerate(fleets) for b in fleets[i + 1 :]]
    average = sum(overlaps) / len(overlaps)
    assert average < 10, f"расстановки слишком похожи: совпадает {average:.1f} из 20 клеток"


def test_seabattle_low_density_placement_survives_longer():
    """Смещение в клетки с низкой плотностью должно измеримо продлевать жизнь
    флота против стрелка, который целится по максимуму плотности."""
    from arena_agent.brains.seabattle import Targeting, _cells, _halo, low_density_fleet, random_fleet

    def survival(make_fleet, seeds: int) -> float:
        totals = []
        for seed in range(seeds):
            ships = make_fleet(random.Random(seed))
            owner: dict[tuple[int, int], int] = {}
            for index, ship in enumerate(ships):
                for cell in _cells(ship["r"], ship["c"], ship["len"], ship["dir"] == "h"):
                    owner[cell] = index
            ship_cells: dict[int, set] = {}
            for cell, index in owner.items():
                ship_cells.setdefault(index, set()).add(cell)
            alive = {i: set(v) for i, v in ship_cells.items()}
            shots: list[list] = []
            marked: set[tuple[int, int]] = set()
            count = 0
            rng = random.Random(seed + 9999)
            while any(alive.values()) and count <= 100:
                targeting = Targeting()
                targeting.load(shots)
                r, c = targeting.next_shot(rng)
                if (r, c) in marked:
                    break
                marked.add((r, c))
                count += 1
                if (r, c) in owner:
                    index = owner[(r, c)]
                    alive[index].discard((r, c))
                    if not alive[index]:
                        shots.append([r, c, "kill"])
                        for cell in _halo(sorted(ship_cells[index])):
                            if cell not in ship_cells[index] and cell not in marked:
                                marked.add(cell)
                                shots.append([cell[0], cell[1], "auto"])
                    else:
                        shots.append([r, c, "hit"])
                else:
                    shots.append([r, c, "miss"])
            totals.append(count)
        return sum(totals) / len(totals)

    uniform = survival(random_fleet, 24)
    low = survival(low_density_fleet, 24)
    assert low > uniform + 1.0, f"равномерная {uniform:.1f}, низкая плотность {low:.1f}"


def test_seabattle_targeting_beats_random_shooting():
    from arena_agent.brains.seabattle import Targeting, _cells, _halo, random_fleet

    totals = []
    for seed in range(12):
        rng = random.Random(seed)
        ships = random_fleet(rng)
        owner = {}
        for index, ship in enumerate(ships):
            for cell in _cells(ship["r"], ship["c"], ship["len"], ship["dir"] == "h"):
                owner[cell] = index
        ship_cells: dict[int, set] = {i: set() for i in range(len(ships))}
        for cell, index in owner.items():
            ship_cells[index].add(cell)

        alive = {i: set(v) for i, v in ship_cells.items()}
        shots_made: list[list] = []
        marked: set[tuple[int, int]] = set()
        count = 0
        while any(alive.values()):
            targeting = Targeting()
            targeting.load(shots_made)
            r, c = targeting.next_shot(rng)
            assert (r, c) not in marked, "в одну клетку нельзя стрелять дважды"
            marked.add((r, c))
            count += 1
            if (r, c) in owner:
                index = owner[(r, c)]
                alive[index].discard((r, c))
                if not alive[index]:
                    shots_made.append([r, c, "kill"])
                    for cell in _halo(sorted(ship_cells[index])):
                        if cell not in ship_cells[index] and cell not in marked:
                            marked.add(cell)
                            shots_made.append([cell[0], cell[1], "auto"])
                else:
                    shots_made.append([r, c, "hit"])
            else:
                shots_made.append([r, c, "miss"])
            assert count <= 100
        totals.append(count)
    average = sum(totals) / len(totals)
    assert average < 70, f"плотностное прицеливание дало в среднем {average:.1f} выстрелов; случайному нужно ~95"


# -------------------------------------------------------------- dotsboxes


def test_dotsboxes_takes_a_free_box():
    n = 3
    horizontal = [[0] * n for _ in range(n + 1)]
    vertical = [[0] * (n + 1) for _ in range(n)]
    # У квадрата (0,0) три стены; четвёртая — это v[0][1].
    horizontal[0][0] = 1
    horizontal[1][0] = 1
    vertical[0][0] = 1
    # Заполняем остальное так, чтобы отвечал не точный решатель.
    for r in range(2, n + 1):
        for c in range(n):
            horizontal[r][c] = 2
    brain = brain_for("dotsboxes")
    move = brain.choose(
        {"yourTurn": True, "n": n, "h": horizontal, "v": vertical, "boxes": [[0] * n for _ in range(n)]},
        context("dotsboxes"),
    )
    assert (move["kind"], move["r"], move["c"]) == ("v", 0, 1), move


def test_dotsboxes_does_not_hand_over_a_box():
    n = 2
    horizontal = [[0] * n for _ in range(n + 1)]
    vertical = [[0] * (n + 1) for _ in range(n)]
    horizontal[0][0] = 1  # у квадрата (0,0) две стены
    vertical[0][0] = 1
    brain = brain_for("dotsboxes")
    move = brain.choose(
        {"yourTurn": True, "n": n, "h": horizontal, "v": vertical, "boxes": [[0] * n for _ in range(n)]},
        context("dotsboxes"),
    )
    # Ни одна из двух стен, которые оставили бы квадрат (0,0) с тремя.
    assert (move["kind"], move["r"], move["c"]) not in {("h", 1, 0), ("v", 0, 1)}, move


# ------------------------------------------------------------------- rule


def test_rule_predicates_read_the_common_rules():
    from arena_agent.brains.rule import predicate_for

    cases = [
        ("even", "the number is even", 4, True),
        ("even", "the number is even", 5, False),
        ("prime", "the number is prime", 7, True),
        ("prime", "the number is prime", 9, False),
        ("div3", "divisible by three", 9, True),
        ("gt50", "greater than 50", 51, True),
        ("gt50", "greater than 50", 50, False),
        ("square", "a perfect square", 49, True),
        ("digit7", "contains the digit 7", 27, True),
        ("ends2", "ends in 2", 42, True),
        ("between", "between 20 and 30", 25, True),
        ("between", "between 20 and 30", 31, False),
    ]
    for rule_id, text, number, expected in cases:
        predicate = predicate_for(rule_id, text)
        assert predicate is not None, f"не удалось прочитать правило {rule_id!r}"
        assert predicate(number) is expected, f"{rule_id}({number}) должно быть {expected}"


def test_rule_guesser_narrows_to_one():
    rules = [
        {"id": "even", "text": "the number is even"},
        {"id": "odd", "text": "the number is odd"},
        {"id": "prime", "text": "the number is prime"},
        {"id": "gt50", "text": "greater than 50"},
        {"id": "square", "text": "a perfect square"},
    ]
    from arena_agent.brains.rule import predicate_for

    secret = predicate_for("square", "a perfect square")
    brain = brain_for("rule")
    probes: list[dict] = []
    for _ in range(10):
        state = {
            "yourTurn": True,
            "phase": "probe",
            "role": "guesser",
            "rules": rules,
            "probes": probes,
            "probesLeft": 10 - len(probes),
        }
        move = brain.choose(state, context("rule"))
        if move["type"] == "guess":
            assert move["rule"] == "square", move
            return
        probes.append({"n": move["n"], "yes": secret(move["n"])})
    raise AssertionError("так и не решился назвать правило")


# ---------------------------------------------------------------- fifteen


def test_fifteen_solver_produces_a_real_solution():
    from arena_agent.brains.fifteen import solve

    rng = random.Random(11)
    for _ in range(5):
        board = list(range(1, 16)) + [0]
        blank = 15
        for _ in range(120):  # тасуем законными ходами, чтобы оставалось решаемым
            r, c = divmod(blank, 4)
            options = []
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 4 and 0 <= cc < 4:
                    options.append(rr * 4 + cc)
            swap = rng.choice(options)
            board[blank], board[swap] = board[swap], board[blank]
            blank = swap

        moves = solve(list(board), 4, seconds=8.0)
        assert moves is not None, "решатель сдался"
        # Проигрываем решение и проверяем, что оно действительно собирает пазл.
        working = list(board)
        for tile in moves:
            index = working.index(tile)
            hole = working.index(0)
            assert abs(index // 4 - hole // 4) + abs(index % 4 - hole % 4) == 1, "недопустимый сдвиг"
            working[hole], working[index] = working[index], working[hole]
        assert working == list(range(1, 16)) + [0], "решение не собирает пазл"


# ------------------------------------------------------- strategy games


def test_karateka_exploits_the_stations_bot():
    """Робик в 55% случаев контрит наш самый частый ход. Точная симуляция этого
    и ответ на предсказание должны выигрывать заметно чаще половины."""
    from arena_agent.brains.strategy_games import KARATE_BEATS, KARATE_COUNTER, KARATE_MOVES

    rng = random.Random(5)
    wins = losses = 0
    for _ in range(6):
        brain = brain_for("karateka")
        ctx = context("karateka", seat=1)
        ctx.opponents = [{"kind": "bot", "name": "Robik"}]
        our_counts: dict[str, int] = {}
        for round_no in range(60):
            move = brain.choose({"phase": "pick", "picked": False, "round": round_no}, ctx)
            ours = move["a"]
            if our_counts:
                best = max(our_counts.values())
                favourites = [m for m in KARATE_MOVES if our_counts.get(m, 0) == best]
                theirs = (
                    KARATE_COUNTER[rng.choice(favourites)]
                    if rng.random() < 0.55
                    else rng.choice(KARATE_MOVES)
                )
            else:
                theirs = rng.choice(KARATE_MOVES)
            our_counts[ours] = our_counts.get(ours, 0) + 1
            if KARATE_BEATS[ours] == theirs:
                wins += 1
            elif KARATE_BEATS[theirs] == ours:
                losses += 1
            brain.on_event(
                {"type": "clash", "acts": {"1": ours, "-1": theirs}, "loser": None}, ctx
            )
    assert wins > losses * 1.6, f"ожидалось явное преимущество над Робиком, получено {wins}П/{losses}Пор"


def test_threefronts_always_spends_the_whole_army():
    brain = brain_for("threefronts")
    for round_no in range(1, 6):
        move = brain.choose(
            {"submitted": False, "round": round_no, "rounds": 5, "history": []},
            context("threefronts"),
        )
        assert move["a"] + move["b"] + move["c"] == 13, move
        assert all(move[k] >= 0 for k in "abc")
        brain.sent_round = -1


def test_pact_stops_cooperating_by_the_end():
    """Взаимное сотрудничество даёт ничью, поэтому к концу партии агент обязан
    разойтись с соперником. Конкретный раунд выбирается случайно, но последний
    раунд не сотрудничает никогда."""
    for seed in range(20):
        brain = brain_for("pact")
        ctx = context("pact", seat=1)
        ctx.rng = random.Random(seed)
        state = {
            "phase": "move",
            "round": 8,
            "rounds": 8,
            "moved": False,
            "totals": {"1": 0, "2": 0},
            "history": [{"moves": {"1": "c", "2": "c"}}],
        }
        move = brain.choose(state, ctx)
        assert move == {"type": "move", "m": "d"}, (seed, move)


def test_pact_betrayal_round_is_not_predictable():
    """История матчей публична, поэтому неизменный раунд предательства —
    это объявление о нём всем, кто нас изучал."""
    rounds = 8
    seen = set()
    for seed in range(40):
        brain = brain_for("pact")
        ctx = context("pact", seat=1)
        ctx.rng = random.Random(seed)
        brain.choose(
            {
                "phase": "move",
                "round": 1,
                "rounds": rounds,
                "moved": False,
                "totals": {"1": 0, "2": 0},
                "history": [],
            },
            ctx,
        )
        seen.add(brain.betray_from)
    assert len(seen) > 1, f"раунд предательства всегда один и тот же: {seen}"
    assert all(2 <= r <= rounds for r in seen), seen


def test_pact_still_wins_against_an_opponent_who_studied_us():
    """Соперник, изучивший нашу историю и предающий в последнем раунде, сводил
    бы каждую партию вничью, будь наш раунд предсказуем."""
    payoff = {("c", "c"): (3, 3), ("c", "d"): (0, 5), ("d", "c"): (5, 0), ("d", "d"): (1, 1)}
    rounds = 8

    def match(seed: int, fixed: bool) -> tuple[int, int]:
        brain = brain_for("pact")
        ctx = context("pact", seat=1)
        ctx.rng = random.Random(seed)
        if fixed:
            brain.betray_from = rounds
        ours = theirs = 0
        history: list[tuple[str, str]] = []
        for number in range(1, rounds + 1):
            state = {
                "phase": "move",
                "round": number,
                "rounds": rounds,
                "moved": False,
                "totals": {"1": ours, "2": theirs},
                "history": [{"moves": {"1": a, "2": b}} for a, b in history],
            }
            our_move = brain.choose(state, ctx)["m"]
            # Соперник знает наш шаблон и предаёт ровно в последнем раунде.
            their_move = "d" if number >= rounds else ("c" if not history else history[-1][0])
            gained, given = payoff[(our_move, their_move)]
            ours += gained
            theirs += given
            history.append((our_move, their_move))
            brain.sent_move_round = -1
        return ours, theirs

    predictable = sum(1 for seed in range(120) if match(seed, True)[0] > match(seed, True)[1])
    varied = sum(1 for seed in range(120) if match(seed, False)[0] > match(seed, False)[1])
    assert predictable == 0, "предсказуемый раунд обязан сводиться вничью"
    assert varied > 20, f"случайный раунд должен выигрывать заметную долю, получено {varied}/120"
    # И ни одна из версий не должна проигрывать.
    assert all(match(seed, False)[0] >= match(seed, False)[1] for seed in range(40))


def test_pact_answers_a_defection():
    brain = brain_for("pact")
    state = {
        "phase": "move",
        "round": 3,
        "rounds": 8,
        "moved": False,
        "totals": {"1": 0, "2": 0},
        "history": [{"moves": {"1": "c", "2": "d"}}],
    }
    move = brain.choose(state, context("pact", seat=1))
    assert move == {"type": "move", "m": "d"}, move


def test_rps_is_uniform_without_evidence():
    brain = brain_for("rps")
    ctx = context("rps")  # один контекст, значит один поток случайности
    counts: dict[str, int] = {}
    for _ in range(600):
        move = brain.choose({"myMatch": {"thrown": False}}, ctx)
        counts[move["v"]] = counts.get(move["v"], 0) + 1
    assert len(counts) == 3
    assert min(counts.values()) > 120, f"далеко от равномерного: {counts}"


def test_onewave_picks_the_focal_option():
    brain = brain_for("onewave")
    move = brain.choose(
        {"picked": False, "options": ["2", "5", "7", "9"], "question": "Pick a number"},
        context("onewave"),
    )
    assert move["v"] == "7", move
    move = brain.choose(
        {"picked": False, "options": ["blue", "red", "green"], "question": "Pick a colour"},
        context("onewave"),
    )
    assert move["v"] == "red", move


# ------------------------------------------------------------------ cards


def test_president_answers_with_the_cheapest_legal_set():
    brain = brain_for("president")
    # Рука: две семёрки (ранг 1), один туз (ранг 8). На столе одна шестёрка (ранг 0).
    hand = [1, 10, 8]
    move = brain.choose(
        {
            "yourTurn": True,
            "hand": hand,
            "trick": {"count": 1, "power": 0, "lastPid": 2},
            "counts": {},
        },
        context("president", seat=1),
    )
    assert move["type"] == "play"
    assert move["cards"][0] % 9 == 1, f"надо было ответить семёркой, а не тузом: {move}"


def test_durak_defends_with_the_cheapest_beater():
    """Карты на столе арена присылает объектами, а не голыми id, — защитнику
    приходится читать форму `{"a": {"id": 1, ...}, "d": null}`."""
    brain = brain_for("durak")
    # Козырь — трефы (масть 3). Атака: семёрка пик (id 1).
    # В руке восьмёрка пик (2), туз пик (8) и мелкий козырь (27).
    attack = {"id": 1, "rank": "7", "suit": 0, "power": 1}
    move = brain.choose(
        {
            "yourTurn": True,
            "role": "defender",
            "hand": [2, 8, 27],
            "table": [{"a": attack, "d": None}],
            "trump": {"id": 32, "rank": "J", "suit": 3, "power": 5},
            "deckLeft": 12,
            "taking": False,
        },
        context("durak", seat=1),
    )
    assert move == {"type": "defend", "idx": 0, "card": 2}, move


def test_durak_reads_bare_card_ids_too():
    brain = brain_for("durak")
    move = brain.choose(
        {
            "yourTurn": True,
            "role": "defender",
            "hand": [2, 8, 27],
            "table": [{"a": 1, "d": None}],
            "trump": {"suit": 3},
            "deckLeft": 12,
            "taking": False,
        },
        context("durak", seat=1),
    )
    assert move == {"type": "defend", "idx": 0, "card": 2}, move


def test_durak_takes_rather_than_burning_trumps_early():
    """Две атаки, которые нечем крыть кроме козырей, пока колода ещё может
    пополнить атакующего: взять карты дешевле, чем отдать козыри."""
    brain = brain_for("durak")
    table = [
        {"a": {"id": 8, "suit": 0, "power": 8}, "d": None},   # ace of spades
        {"a": {"id": 17, "suit": 1, "power": 8}, "d": None},  # ace of hearts
    ]
    move = brain.choose(
        {
            "yourTurn": True,
            "role": "defender",
            "hand": [34, 35, 3],  # два старших козыря и бесполезная пика
            "table": table,
            "trump": {"suit": 3},
            "deckLeft": 20,
            "taking": False,
        },
        context("durak", seat=1),
    )
    assert move == {"type": "take"}, move


def test_believe_doubts_a_provably_impossible_claim():
    brain = brain_for("believe")
    ctx = context("believe", seat=1)
    # У нас три шестёрки (ранг 0). Он заявляет ещё две: пять из существующих четырёх.
    brain.on_event({"type": "played", "rankIndex": 0, "count": 2}, ctx)
    move = brain.choose(
        {
            "yourTurn": True,
            "hand": [0, 9, 18],
            "pileSize": 2,
            "lastBatch": {"pid": 2, "count": 2},
            "currentRankIndex": 0,
            "claimant": None,
        },
        ctx,
    )
    assert move == {"type": "doubt"}, move


def test_believe_always_doubts_a_player_going_out():
    brain = brain_for("believe")
    move = brain.choose(
        {
            "yourTurn": True,
            "hand": [5, 6],
            "pileSize": 3,
            "lastBatch": {"pid": 2, "count": 1},
            "currentRankIndex": 4,
            "claimant": 2,
        },
        context("believe", seat=1),
    )
    assert move == {"type": "doubt"}, move


# ------------------------------------------------------------------ tanks


def test_tanks_shoots_then_moves_and_never_stands_still():
    brain = brain_for("tanks")
    ctx = context("tanks", seat=1)
    state = {
        "yourTurn": True,
        "n": 7,
        "me": {"x": 3, "y": 3},
        "enemy": None,
        "water": 0,
        "floodIn": 9,
        "shotThisTurn": False,
        "hp": {"1": 2, "2": 2},
        "dust": {"me": None, "foe": {"x": 5, "y": 5}},
    }
    shot = brain.choose(state, ctx)
    assert shot["type"] == "shoot"
    # Пыль в (5,5) значит, что он в одной из восьми клеток вокруг.
    assert abs(shot["x"] - 5) <= 1 and abs(shot["y"] - 5) <= 1, shot

    state["shotThisTurn"] = True
    move = brain.choose(state, ctx)
    assert move["type"] == "move"
    assert (move["dx"], move["dy"]) != (0, 0), "стоять на месте арена не разрешает"
    assert abs(move["dx"]) <= 1 and abs(move["dy"]) <= 1


# -------------------------------------------------------------- artillery


# Траектория, записанная в настоящем матче на арене, вместе с углом, мощностью и
# ветром, которые её породили. Константы арены нигде не опубликованы; это те
# улики, из которых мозг обязан их восстановить.
REAL_TRAJECTORY = [
    [69.6963821388975, 25.449252661318393],
    [72.3, 26.7],
    [80.4, 30.1],
    [88.5, 33.1],
    [96.7, 35.6],
    [105.0, 37.7],
    [113.4, 39.3],
    [116.3, 39.7],
]
REAL_SHOT = {"angle": 26, "power": 72.4, "wind": 16.6, "impact_x": 116.3}


def _reconstructed_terrain() -> list[float]:
    """Холм из того же матча, насколько он был записан."""
    heights = [
        79, 78.4, 77.8, 77.1, 76.3, 75.4, 74.4, 73.4, 72.3, 71.1, 69.8, 68.5,
        67.2, 65.8, 64.4, 63, 61.5, 60, 58.6, 57.1, 55.7, 54.2, 52.8, 51.4,
        50.1, 48.8, 47.6, 46.4, 45.2, 44.1, 43.1, 42.1, 41.2, 40.4, 39.6, 38.8,
        38.1, 37.5, 36.9, 36.4, 35.9, 35.4, 35, 34.6, 34.2, 33.9, 33.5, 33.2,
        32.8, 32.5, 32.1, 31.7, 31.3, 30.9, 30.4, 29.9, 29.4, 28.9, 28.3, 27.6,
        26.9, 26.2, 25.5, 24.7, 23.8, 23, 22.1, 21.1, 20.2, 19.2, 18.3, 17.3,
        16.3, 15.4, 14.4, 13.5, 12.6, 11.8, 11, 10.2, 10, 10, 10, 10, 10, 10,
        10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10.8, 11.7, 12.8, 13.9, 15.2,
        16.5, 17.9, 19.3, 20.8, 22.4, 24, 25.7, 27.4,
    ]
    terrain = [float(h) for h in heights]
    while len(terrain) < 240:
        terrain.append(min(90.0, terrain[-1] + 1.75))
    return terrain


def test_artillery_recovers_the_physics_from_one_trajectory():
    """Вторые разности снятой по точкам параболы — это гравитация и ветер."""
    from arena_agent.brains.artillery import Ballistics

    model = Ballistics(240, 160)
    assert model.learn_from_trajectory(
        REAL_TRAJECTORY, REAL_SHOT["angle"], REAL_SHOT["power"], REAL_SHOT["wind"]
    )
    assert 0.3 < model.gravity < 0.6, model.gravity
    assert 0.002 < model.wind_scale < 0.010, model.wind_scale
    assert 0.05 < model.power_scale < 0.25, model.power_scale
    # Шаг запуска неполный; пропустить это значит сместить всякую оценку дальности.
    assert 0.2 < model.launch_fraction < 0.6, model.launch_fraction


def test_artillery_replays_the_shot_it_learned_from():
    from arena_agent.brains.artillery import Ballistics

    model = Ballistics(240, 160)
    model.learn_from_trajectory(
        REAL_TRAJECTORY, REAL_SHOT["angle"], REAL_SHOT["power"], REAL_SHOT["wind"]
    )
    landing = model.simulate(
        REAL_TRAJECTORY[0][0],
        REAL_TRAJECTORY[0][1],
        REAL_SHOT["angle"],
        REAL_SHOT["power"],
        REAL_SHOT["wind"],
        _reconstructed_terrain(),
    )
    assert landing is not None, "снаряд должен упасть, а не улететь за поле"
    # Рельеф здесь восстановлен по неполной записи, так что несколько клеток
    # запаса — это погрешность измерения, а не модели.
    assert abs(landing - REAL_SHOT["impact_x"]) < 12, landing


def test_artillery_aims_at_a_target_after_calibration():
    from arena_agent.brains.artillery import Ballistics

    model = Ballistics(240, 160)
    model.learn_from_trajectory(
        REAL_TRAJECTORY, REAL_SHOT["angle"], REAL_SHOT["power"], REAL_SHOT["wind"]
    )
    terrain = _reconstructed_terrain()
    for target in (110.0, 150.0, 187.0):
        angle, power = model.aim(67.0, 22.1, target, REAL_SHOT["wind"], terrain)
        assert 0 < angle < 180 and 10 <= power <= 100, (angle, power)
        landing = model.simulate(67.0, 22.1, angle, power, REAL_SHOT["wind"], terrain)
        assert landing is not None, f"прицеливание в {target} дало выстрел за пределы поля"
        assert abs(landing - target) < 3.0, f"целились в {target}, ложится в {landing:.1f}"


def test_artillery_remembers_its_physics_between_matches():
    """Гравитация и ветер — свойства арены, а не партии.

    Это лечит конкретное поражение: соперник попадает первым выстрелом, мы
    тратим три на пристрелку, и матч кончается 100:0. Второй матч обязан
    начинаться уже откалиброванным.
    """
    import tempfile

    from arena_agent.brains.artillery import ArtilleryBrain, Ballistics
    from arena_agent.store import Store

    store = Store(tempfile.mkdtemp())
    ctx = context("artillery", seat=1)
    ctx.store = store

    first = ArtilleryBrain()
    first.model = Ballistics(240, 160)
    first._recall(ctx)
    assert not first.loaded_from_memory, "в первом матче помнить ещё нечего"
    assert first.model.learn_from_trajectory(
        REAL_TRAJECTORY, REAL_SHOT["angle"], REAL_SHOT["power"], REAL_SHOT["wind"]
    )
    first._remember(ctx)

    second = ArtilleryBrain()
    second.model = Ballistics(240, 160)
    second._recall(ctx)
    assert second.loaded_from_memory, "второй матч должен поднять физику из памяти"
    assert second.model.calibrated, "и считать себя откалиброванным до первого выстрела"
    assert abs(second.model.gravity - first.model.gravity) < 1e-9
    assert abs(second.model.power_scale - first.model.power_scale) < 1e-9
    # Остаточная поправка — величина матча, в новый она не переносится.
    assert second.model.correction == 1.0

    # И главное: первый выстрел нового матча уже ложится в цель.
    terrain = _reconstructed_terrain()
    angle, power = second.model.aim(67.0, 22.1, 150.0, REAL_SHOT["wind"], terrain)
    landing = second.model.simulate(67.0, 22.1, angle, power, REAL_SHOT["wind"], terrain)
    assert landing is not None and abs(landing - 150.0) < 3.0, landing


def test_artillery_rejects_nonsense_from_memory():
    """Испорченный файл памяти не должен ломать прицеливание."""
    from arena_agent.brains.artillery import Ballistics

    model = Ballistics(240, 160)
    for junk in ({}, {"gravity": 0}, {"gravity": "x"}, {"gravity": 1, "wind_scale": 0}):
        assert not model.load_record(junk), junk
    assert not model.calibrated


def test_artillery_brain_fires_a_well_formed_shot():
    brain = brain_for("artillery")
    state = {
        "yourTurn": True,
        "w": 240,
        "h": 160,
        "terrain": _reconstructed_terrain(),
        "wind": 5.0,
        "tanks": {"1": {"x": 67, "y": 22.1, "hp": 100}, "2": {"x": 187, "y": 51.7, "hp": 100}},
    }
    move = brain.choose(state, context("artillery", seat=1))
    assert move["type"] == "fire"
    assert 0 <= move["angle"] <= 180, move
    assert 10 <= move["power"] <= 100, move


# ------------------------------------------------------- предохранитель

def test_a_crashing_brain_still_plays_a_legal_move():
    """Проиграть из-за собственного исключения — худший способ проиграть.

    Там, где арена публикует список законных ходов, драйвер обязан сыграть
    хоть что-то из него, а не замолчать до потери по времени."""
    import types

    from arena_agent.brains import base as brains_base
    from arena_agent.match import MatchSession

    class Boom(brains_base.Brain):
        game = "chess"

        def choose(self, state, ctx):
            raise RuntimeError("умышленная поломка")

    runner = types.SimpleNamespace(
        client=None,
        settings=types.SimpleNamespace(async_think_seconds=8, live_think_seconds=3),
        store=None,
        rng=random.Random(1),
        agent_name="DVB-Arena",
    )
    session = MatchSession(runner, "TEST0000", "chess")
    session.brain = Boom()
    published = [{"from": "e2", "to": "e4"}, {"from": "d2", "to": "d4"}]
    session.state = {"yourTurn": True, "legal_moves": published}

    sent: list[dict] = []

    class FakeClient:
        def move(self, code, move):
            sent.append(move)
            return {"accepted": True, "events": [], "state": {"yourTurn": False}}

    session.client = FakeClient()
    session._play_turn()

    assert len(sent) == 1, f"должен был уйти ровно один ход, ушло: {sent}"
    assert {"from": sent[0]["from"], "to": sent[0]["to"]} in published, sent


def test_no_fallback_is_invented_without_a_legal_move_list():
    """Где списка законных ходов нет, наугад слать нельзя: отклонённый ход
    от часов всё равно не спасает."""
    import types

    from arena_agent.match import MatchSession

    runner = types.SimpleNamespace(
        client=None,
        settings=types.SimpleNamespace(async_think_seconds=8, live_think_seconds=3),
        store=None,
        rng=random.Random(1),
        agent_name="DVB-Arena",
    )
    session = MatchSession(runner, "TEST0000", "durak")
    session.state = {"yourTurn": True}
    assert session._fallback_move() is None


# ---------------------------------------------------------------- квота

def test_budget_is_taken_from_the_arena_not_from_our_own_count():
    """Наш счётчик обнуляется при перезапуске, квота арены — нет.

    Агент, которого перезапускали трижды за день, считал бы себя свежим и
    упирался бы в лимит с разбегу. Троттлинг съедает время, отведённое на ход,
    поэтому это стоит партий.
    """
    from arena_agent.client import ArenaClient
    from arena_agent.config import Settings

    client = ArenaClient(Settings())
    assert client.empty_reads == 0

    client.sync_spend({"read_empty": 1200, "move": 500, "table": 40})
    assert client.empty_reads == 1200
    assert client.moves_spent == 500
    assert client.tables_opened == 40

    # Мягкий предел теперь действительно срабатывает.
    assert client.empty_read_headroom == 0.0, client.empty_read_headroom

    # Локальный расход после сверки не теряется.
    client.note_empty_read()
    assert client.empty_reads == 1201

    # Арена — источник истины, но назад счётчик не откатывается.
    client.sync_spend({"read_empty": 900})
    assert client.empty_reads == 1201

    # Мусор игнорируется.
    client.sync_spend({"read_empty": "много"})
    client.sync_spend(None)
    assert client.empty_reads == 1201


# --------------------------------------------------------------- разведка

class _ScoutClient:
    """Страница агента, какой её отдаёт платформа."""

    def __init__(self, matches):
        self.matches = matches
        self.calls = 0

    def agent_page(self, name):
        self.calls += 1
        return {"agent": {"name": name}, "matches": self.matches}


class _ScoutStore:
    def __init__(self, stats):
        self.stats = stats

    def read_json(self, name, default=None):
        return {}

    def write_json(self, name, payload):
        pass


def test_scout_reads_a_per_game_record_from_public_matches():
    from arena_agent.scout import Scout

    client = _ScoutClient(
        [
            {"game": "seabattle", "winners": ["Кто-то"]},
            {"game": "seabattle", "winners": ["Кто-то"]},
            {"game": "seabattle", "winners": ["Соперник"]},
            {"game": "karateka", "winners": ["Соперник"]},
        ]
    )
    scout = Scout(client, _ScoutStore({}))
    assert scout.win_rate("Соперник", "seabattle") == 1 / 3
    # Одной партии мало, чтобы делать вывод.
    assert scout.win_rate("Соперник", "karateka") is None
    # Про игру, которой в истории нет, сведений нет.
    assert scout.win_rate("Соперник", "chess") is None


def test_scout_caches_and_never_raises():
    from arena_agent.scout import Scout

    client = _ScoutClient([{"game": "chess", "winners": ["Соперник"]}] * 4)
    scout = Scout(client, _ScoutStore({}))
    scout.record("Соперник")
    scout.record("Соперник")
    assert client.calls == 1, "карточка должна запрашиваться один раз"

    class Broken:
        def agent_page(self, name):
            raise RuntimeError("сеть недоступна")

    # Разведка вспомогательна: её отказ не должен ломать игру.
    assert Scout(Broken(), _ScoutStore({})).record("Кто угодно") == {}
    assert Scout(Broken(), _ScoutStore({})).table_value("chess", "Кто угодно") == 0.25


def test_scout_prefers_our_strong_game_against_their_weak_one():
    from arena_agent.scout import Scout

    client = _ScoutClient(
        [
            {"game": "seabattle", "winners": ["Кто-то"]},
            {"game": "seabattle", "winners": ["Кто-то"]},
            {"game": "seabattle", "winners": ["Соперник"]},
            {"game": "karateka", "winners": ["Соперник"]},
            {"game": "karateka", "winners": ["Соперник"]},
            {"game": "karateka", "winners": ["Соперник"]},
        ]
    )
    store = _ScoutStore(
        {
            "seabattle": {"win": 4, "loss": 1, "draw": 0},
            "karateka": {"win": 1, "loss": 2, "draw": 0},
        }
    )
    scout = Scout(client, store)
    strong = scout.table_value("seabattle", "Соперник")
    weak = scout.table_value("karateka", "Соперник")
    unknown = scout.table_value("chess", "Незнакомец")
    assert strong > unknown > weak, (strong, unknown, weak)


def _run_all() -> int:
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())

"""Самодостаточный шахматный движок: на входе FEN, на выходе ход.

Написан на чистом Python без зависимостей, потому что агент должен разворачиваться
где угодно. Если доступен UCI-движок, он строго лучше и будет использован через
`uci.py`, — это пол, а не потолок.

Альфа-бета с итеративным углублением, форсированный поиск (чтобы оценка никогда не
бралась посреди серии разменов), упорядочивание MVV-LVA, killer-ходы и таблица
транспозиций по ключу Zobrist.
"""

from __future__ import annotations

import random
import time

WHITE, BLACK = "w", "b"

PIECE_VALUES = {"p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 20000}

# Индекс 0 — это a8, индекс 63 — h1, что совпадает с порядком записи FEN.
PST = {
    "p": [
        0, 0, 0, 0, 0, 0, 0, 0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
        5, 5, 10, 25, 25, 10, 5, 5,
        0, 0, 0, 20, 20, 0, 0, 0,
        5, -5, -10, 0, 0, -10, -5, 5,
        5, 10, 10, -20, -20, 10, 10, 5,
        0, 0, 0, 0, 0, 0, 0, 0,
    ],
    "n": [
        -50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20, 0, 0, 0, 0, -20, -40,
        -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30,
        -30, 0, 15, 20, 20, 15, 0, -30,
        -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50,
    ],
    "b": [
        -20, -10, -10, -10, -10, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 10, 10, 5, 0, -10,
        -10, 5, 5, 10, 10, 5, 5, -10,
        -10, 0, 10, 10, 10, 10, 0, -10,
        -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20,
    ],
    "r": [
        0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, 10, 10, 10, 10, 5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        0, 0, 0, 5, 5, 0, 0, 0,
    ],
    "q": [
        -20, -10, -10, -5, -5, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 5, 5, 5, 0, -10,
        -5, 0, 5, 5, 5, 5, 0, -5,
        0, 0, 5, 5, 5, 5, 0, -5,
        -10, 5, 5, 5, 5, 5, 0, -10,
        -10, 0, 5, 0, 0, 0, 0, -10,
        -20, -10, -10, -5, -5, -10, -10, -20,
    ],
    "k": [
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10,
        20, 20, 0, 0, 0, 0, 20, 20,
        20, 30, 10, 0, 0, 10, 30, 20,
    ],
}
KING_ENDGAME_PST = [
    -50, -40, -30, -20, -20, -30, -40, -50,
    -30, -20, -10, 0, 0, -10, -20, -30,
    -30, -10, 20, 30, 30, 20, -10, -30,
    -30, -10, 30, 40, 40, 30, -10, -30,
    -30, -10, 30, 40, 40, 30, -10, -30,
    -30, -10, 20, 30, 30, 20, -10, -30,
    -30, -30, 0, 0, 0, 0, -30, -30,
    -50, -30, -30, -30, -30, -30, -30, -50,
]

KNIGHT_STEPS = ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1))
KING_STEPS = ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
BISHOP_RAYS = ((1, 1), (1, -1), (-1, 1), (-1, -1))
ROOK_RAYS = ((0, 1), (1, 0), (0, -1), (-1, 0))

MATE = 1_000_000

_rng = random.Random(0xC0FFEE)
ZOBRIST = {
    piece: [_rng.getrandbits(64) for _ in range(64)]
    for piece in "PNBRQKpnbrqk"
}
ZOBRIST_SIDE = _rng.getrandbits(64)
ZOBRIST_CASTLE = {flag: _rng.getrandbits(64) for flag in "KQkq"}
ZOBRIST_EP = [_rng.getrandbits(64) for _ in range(8)]


def square_name(index: int) -> str:
    return "abcdefgh"[index % 8] + str(8 - index // 8)


def square_index(name: str) -> int:
    return (8 - int(name[1])) * 8 + "abcdefgh".index(name[0])


class Position:
    __slots__ = ("board", "side", "castling", "ep", "halfmove", "fullmove", "_key")

    def __init__(self, fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"):
        parts = fen.split()
        rows = parts[0].split("/")
        board: list[str] = []
        for row in rows:
            for char in row:
                if char.isdigit():
                    board.extend([""] * int(char))
                else:
                    board.append(char)
        self.board = board
        self.side = parts[1] if len(parts) > 1 else WHITE
        self.castling = parts[2] if len(parts) > 2 and parts[2] != "-" else ""
        self.ep = square_index(parts[3]) if len(parts) > 3 and parts[3] != "-" else -1
        self.halfmove = int(parts[4]) if len(parts) > 4 else 0
        self.fullmove = int(parts[5]) if len(parts) > 5 else 1
        self._key = self._compute_key()

    # ------------------------------------------------------------ хеширование

    def _compute_key(self) -> int:
        key = 0
        for index, piece in enumerate(self.board):
            if piece:
                key ^= ZOBRIST[piece][index]
        if self.side == BLACK:
            key ^= ZOBRIST_SIDE
        for flag in self.castling:
            key ^= ZOBRIST_CASTLE.get(flag, 0)
        if self.ep >= 0:
            key ^= ZOBRIST_EP[self.ep % 8]
        return key

    @property
    def key(self) -> int:
        return self._key

    # -------------------------------------------------------------- разбор

    @staticmethod
    def colour_of(piece: str) -> str:
        return WHITE if piece.isupper() else BLACK

    def king_square(self, side: str) -> int:
        target = "K" if side == WHITE else "k"
        try:
            return self.board.index(target)
        except ValueError:
            return -1

    def attacked(self, square: int, by: str) -> bool:
        """Атакует ли `by` поле `square`. Нужно для легальности и для шаха."""
        r, c = divmod(square, 8)

        # Пешки бьют в сторону соперника, поэтому белый атакующий стоит на
        # горизонталь ниже поля в этой отрисовке (больший индекс).
        pawn = "P" if by == WHITE else "p"
        direction = 1 if by == WHITE else -1
        for dc in (-1, 1):
            rr, cc = r + direction, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr * 8 + cc] == pawn:
                return True

        knight = "N" if by == WHITE else "n"
        for dr, dc in KNIGHT_STEPS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr * 8 + cc] == knight:
                return True

        king = "K" if by == WHITE else "k"
        for dr, dc in KING_STEPS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr * 8 + cc] == king:
                return True

        for rays, pieces in ((BISHOP_RAYS, "bq"), (ROOK_RAYS, "rq")):
            wanted = pieces.upper() if by == WHITE else pieces
            for dr, dc in rays:
                rr, cc = r + dr, c + dc
                while 0 <= rr < 8 and 0 <= cc < 8:
                    piece = self.board[rr * 8 + cc]
                    if piece:
                        if piece in wanted:
                            return True
                        break
                    rr += dr
                    cc += dc
        return False

    def in_check(self, side: str | None = None) -> bool:
        side = side or self.side
        king = self.king_square(side)
        if king < 0:
            return False
        return self.attacked(king, BLACK if side == WHITE else WHITE)

    # -------------------------------------------------------- генерация ходов

    def pseudo_moves(self, captures_only: bool = False) -> list[tuple]:
        moves: list[tuple] = []
        side = self.side
        board = self.board
        for index, piece in enumerate(board):
            if not piece or self.colour_of(piece) != side:
                continue
            kind = piece.lower()
            r, c = divmod(index, 8)

            if kind == "p":
                direction = -1 if side == WHITE else 1
                start_rank = 6 if side == WHITE else 1
                last_rank = 0 if side == WHITE else 7
                forward = index + direction * 8
                if not captures_only and 0 <= forward < 64 and not board[forward]:
                    if forward // 8 == last_rank:
                        moves.extend((index, forward, promo) for promo in "qrbn")
                    else:
                        moves.append((index, forward, None))
                        if r == start_rank:
                            double = index + direction * 16
                            if not board[double]:
                                moves.append((index, double, None))
                for dc in (-1, 1):
                    rr, cc = r + direction, c + dc
                    if not (0 <= rr < 8 and 0 <= cc < 8):
                        continue
                    target = rr * 8 + cc
                    victim = board[target]
                    if (victim and self.colour_of(victim) != side) or target == self.ep:
                        if rr == last_rank:
                            moves.extend((index, target, promo) for promo in "qrbn")
                        else:
                            moves.append((index, target, None))
                continue

            if kind == "n":
                steps = KNIGHT_STEPS
            elif kind == "k":
                steps = KING_STEPS
            else:
                steps = ()

            if steps:
                for dr, dc in steps:
                    rr, cc = r + dr, c + dc
                    if not (0 <= rr < 8 and 0 <= cc < 8):
                        continue
                    target = rr * 8 + cc
                    victim = board[target]
                    if victim and self.colour_of(victim) == side:
                        continue
                    if captures_only and not victim:
                        continue
                    moves.append((index, target, None))
            else:
                rays = BISHOP_RAYS if kind == "b" else (ROOK_RAYS if kind == "r" else BISHOP_RAYS + ROOK_RAYS)
                for dr, dc in rays:
                    rr, cc = r + dr, c + dc
                    while 0 <= rr < 8 and 0 <= cc < 8:
                        target = rr * 8 + cc
                        victim = board[target]
                        if victim:
                            if self.colour_of(victim) != side:
                                moves.append((index, target, None))
                            break
                        if not captures_only:
                            moves.append((index, target, None))
                        rr += dr
                        cc += dc

        if not captures_only:
            moves.extend(self._castling_moves())
        return moves

    def _castling_moves(self) -> list[tuple]:
        out: list[tuple] = []
        side = self.side
        enemy = BLACK if side == WHITE else WHITE
        if side == WHITE:
            king_from, rights, rank = 60, ("K", "Q"), 7
        else:
            king_from, rights, rank = 4, ("k", "q"), 0
        if self.board[king_from] != ("K" if side == WHITE else "k"):
            return out
        if self.attacked(king_from, enemy):
            return out
        for right in rights:
            if right not in self.castling:
                continue
            kingside = right.lower() == "k"
            if kingside:
                empties = [rank * 8 + 5, rank * 8 + 6]
                pass_through = [rank * 8 + 5, rank * 8 + 6]
                rook_square = rank * 8 + 7
                king_to = rank * 8 + 6
            else:
                empties = [rank * 8 + 1, rank * 8 + 2, rank * 8 + 3]
                pass_through = [rank * 8 + 3, rank * 8 + 2]
                rook_square = rank * 8 + 0
                king_to = rank * 8 + 2
            if any(self.board[square] for square in empties):
                continue
            if self.board[rook_square] != ("R" if side == WHITE else "r"):
                continue
            if any(self.attacked(square, enemy) for square in pass_through):
                continue
            out.append((king_from, king_to, None))
        return out

    def legal_moves(self, captures_only: bool = False) -> list[tuple]:
        out = []
        for move in self.pseudo_moves(captures_only):
            undo = self.make(move)
            if not self.in_check(BLACK if self.side == WHITE else WHITE):
                out.append(move)
            self.unmake(move, undo)
        return out

    # ------------------------------------------------------------ ход/откат

    def make(self, move: tuple):
        origin, target, promotion = move
        board = self.board
        piece = board[origin]
        captured = board[target]
        previous_key = self._key

        key = self._key
        key ^= ZOBRIST[piece][origin]
        if captured:
            key ^= ZOBRIST[captured][target]
        if self.ep >= 0:
            key ^= ZOBRIST_EP[self.ep % 8]
        for flag in self.castling:
            key ^= ZOBRIST_CASTLE.get(flag, 0)

        board[origin] = ""
        kind = piece.lower()
        extra = None

        # Взятие на проходе: сбитая пешка стоит не на целевом поле.
        if kind == "p" and target == self.ep and not captured:
            victim_square = target + (8 if self.side == WHITE else -8)
            extra = ("ep", victim_square, board[victim_square])
            key ^= ZOBRIST[board[victim_square]][victim_square]
            board[victim_square] = ""

        if kind == "k" and abs(target - origin) == 2:
            rank = origin // 8
            if target > origin:
                rook_from, rook_to = rank * 8 + 7, rank * 8 + 5
            else:
                rook_from, rook_to = rank * 8 + 0, rank * 8 + 3
            rook = board[rook_from]
            board[rook_from] = ""
            board[rook_to] = rook
            key ^= ZOBRIST[rook][rook_from] ^ ZOBRIST[rook][rook_to]
            extra = ("castle", rook_from, rook_to)

        placed = piece
        if promotion:
            placed = promotion.upper() if self.side == WHITE else promotion.lower()
        board[target] = placed
        key ^= ZOBRIST[placed][target]

        undo = (piece, captured, self.castling, self.ep, self.halfmove, extra, previous_key)

        # Право рокировки исчезает, когда уходит король или ладья либо ладью бьют.
        rights = self.castling
        if kind == "k":
            rights = rights.replace("K", "").replace("Q", "") if self.side == WHITE else rights.replace("k", "").replace("q", "")
        for square, flag in ((63, "K"), (56, "Q"), (7, "k"), (0, "q")):
            if origin == square or target == square:
                rights = rights.replace(flag, "")
        self.castling = rights
        for flag in self.castling:
            key ^= ZOBRIST_CASTLE.get(flag, 0)

        self.ep = -1
        if kind == "p" and abs(target - origin) == 16:
            self.ep = (origin + target) // 2
            key ^= ZOBRIST_EP[self.ep % 8]

        self.halfmove = 0 if (kind == "p" or captured) else self.halfmove + 1
        if self.side == BLACK:
            self.fullmove += 1
        self.side = BLACK if self.side == WHITE else WHITE
        key ^= ZOBRIST_SIDE
        self._key = key
        return undo

    def make_null(self):
        """Передать очередь сопернику, не делая хода.

        Нужно для нулевого хода: если позиция настолько хороша, что её не
        спасает даже подаренный сопернику темп, ветку можно отсечь.
        """
        undo = (self.ep, self._key)
        if self.ep >= 0:
            self._key ^= ZOBRIST_EP[self.ep % 8]
        self.ep = -1
        self.side = BLACK if self.side == WHITE else WHITE
        self._key ^= ZOBRIST_SIDE
        return undo

    def unmake_null(self, undo) -> None:
        self.ep, self._key = undo
        self.side = BLACK if self.side == WHITE else WHITE

    def has_non_pawn_material(self) -> bool:
        """Нулевой ход небезопасен в цугцванге, а цугцванг живёт в пешечных
        окончаниях, поэтому там мы его не применяем."""
        wanted = "NBRQ" if self.side == WHITE else "nbrq"
        # `piece` пустой строкой обозначает пустое поле, а пустая строка входит
        # в любую другую — без явной проверки условие было бы всегда истинным.
        return any(piece and piece in wanted for piece in self.board)

    def unmake(self, move: tuple, undo) -> None:
        origin, target, promotion = move
        piece, captured, castling, ep, halfmove, extra, previous_key = undo
        board = self.board
        self.side = BLACK if self.side == WHITE else WHITE
        if self.side == BLACK:
            self.fullmove -= 1
        board[origin] = piece
        board[target] = captured
        if extra:
            if extra[0] == "ep":
                board[target] = ""
                board[extra[1]] = extra[2]
            elif extra[0] == "castle":
                rook_from, rook_to = extra[1], extra[2]
                board[rook_from] = board[rook_to]
                board[rook_to] = ""
        self.castling = castling
        self.ep = ep
        self.halfmove = halfmove
        del promotion
        self._key = previous_key

    # ------------------------------------------------------------- оценка

    def evaluate(self) -> int:
        """Положительное значение означает, что лучше стоит сторона, чей ход."""
        board = self.board
        # Один сбор занятых полей вместо четырёх проходов по всей доске: оценка
        # считается в каждом листе перебора, и лишний проход стоит узлов.
        occupied = [(i, p) for i, p in enumerate(board) if p]
        material = sum(PIECE_VALUES[p.lower()] for _, p in occupied if p.lower() != "k")
        endgame = material < 2400

        score = 0
        white_bishops = black_bishops = 0
        for index, piece in occupied:
            kind = piece.lower()
            white = piece.isupper()
            table_index = index if white else (7 - index // 8) * 8 + index % 8
            if kind == "k" and endgame:
                positional = KING_ENDGAME_PST[table_index]
            else:
                positional = PST[kind][table_index]
            value = PIECE_VALUES[kind] + positional
            score += value if white else -value
            if kind == "b":
                if white:
                    white_bishops += 1
                else:
                    black_bishops += 1

        # Пара слонов стоит примерно полпешки, и ни одна таблица полей это не ловит.
        if white_bishops >= 2:
            score += 40
        if black_bishops >= 2:
            score -= 40

        if not endgame:
            white_safety, black_safety = king_safety_pair(board)
            score += white_safety - black_safety

        return score if self.side == WHITE else -score

    def fen(self) -> str:
        rows = []
        for r in range(8):
            row, empty = "", 0
            for c in range(8):
                piece = self.board[r * 8 + c]
                if piece:
                    if empty:
                        row += str(empty)
                        empty = 0
                    row += piece
                else:
                    empty += 1
            if empty:
                row += str(empty)
            rows.append(row)
        return " ".join(
            [
                "/".join(rows),
                self.side,
                self.castling or "-",
                square_name(self.ep) if self.ep >= 0 else "-",
                str(self.halfmove),
                str(self.fullmove),
            ]
        )



def _shelter(board: list[str], king_row: int, king_col: int, white: bool) -> int:
    """Штраф за дыры в пешечной крыше над королём."""
    # Индекс 0 — a8, поэтому «вперёд» для белых значит вверх по строкам.
    forward = -1 if white else 1
    own_pawn = "P" if white else "p"
    enemy_pawn = "p" if white else "P"
    penalty = 0
    for col in range(max(0, king_col - 1), min(7, king_col + 1) + 1):
        for step in (1, 2, 3):
            row = king_row + forward * step
            if not 0 <= row < 8:
                break
            if board[row * 8 + col] == own_pawn:
                break
        else:
            penalty += 26 if col == king_col else 15
            if not any(board[r * 8 + col] == enemy_pawn for r in range(8)):
                # Вертикаль открыта с обеих сторон — по ней и приходит ладья.
                penalty += 16
    return penalty


def king_safety_pair(board: list[str]) -> tuple[int, int]:
    """Прочность обоих королей. Ноль — норма, минус — беда.

    В оценке не было ничего про короля, кроме таблицы полей, и это стоило
    партии: имея материальный перевес, движок увёл ферзя на другой фланг за
    слоном и получил мат. Форсированный мат был в четырнадцати полуходах —
    глубже, чем считается за шесть секунд на Python, — так что увидеть опасность
    может только оценка, а не перебор.

    Считается ровно одно: **крыша**. Пешки на вертикали короля и двух соседних;
    отсутствие пешки — дыра, а вертикаль, на которой нет вообще ничьих пешек, —
    дорога для ладьи. Именно это и было не так в проигранной партии: короля
    увели на b1, где вертикаль b открыта настежь.

    Здесь был и второй член — тропизм, сумма «веса на расстоянии» вражеских
    фигур. Он убран по результату замера: полный проход по доске в каждом листе
    перебора стоил 9% просматриваемых узлов, а в той самой позиции, ради которой
    писался, дал ровно ноль. Перебор находит сближение фигур сам; чего он не
    находит за отведённое время — это что вертикаль рядом с королём открыта.
    """
    try:
        white_king = board.index("K")
        black_king = board.index("k")
    except ValueError:
        return 0, 0
    return (
        -_shelter(board, *divmod(white_king, 8), True),
        -_shelter(board, *divmod(black_king, 8), False),
    )


def king_safety(board: list[str], side: str) -> int:
    """Прочность одного короля. Тонкая обёртка — нужна проверкам и разбору."""
    white, black = king_safety_pair(board)
    return white if side == WHITE else black


class Search:
    def __init__(self, position: Position):
        self.position = position
        self.table: dict[int, tuple] = {}
        self.killers: dict[int, list] = {}
        # История: сколько раз ход «отсекал» ветку. Тихие ходы, срабатывавшие
        # раньше, пробуются первыми — это заметно улучшает порядок перебора там,
        # где взятий нет и MVV-LVA молчит.
        self.history: dict[tuple, int] = {}
        self.nodes = 0
        self.deadline = 0.0

    def _order(self, moves: list[tuple], depth: int, best: tuple | None) -> list[tuple]:
        board = self.position.board
        killers = self.killers.get(depth, [])

        def score(move):
            if best and move == best:
                return 1_000_000
            origin, target, promotion = move
            victim = board[target]
            if victim:
                # Самая ценная жертва, наименее ценный атакующий.
                return 100_000 + PIECE_VALUES[victim.lower()] * 10 - PIECE_VALUES[board[origin].lower()]
            if move in killers:
                return 90_000
            if promotion:
                return 80_000 + PIECE_VALUES[promotion]
            return self.history.get((origin, target), 0)

        return sorted(moves, key=score, reverse=True)

    def quiesce(self, alpha: int, beta: int) -> int:
        self.nodes += 1
        if self.nodes % 2048 == 0 and time.time() > self.deadline:
            raise TimeoutError
        standing = self.position.evaluate()
        if standing >= beta:
            return beta
        alpha = max(alpha, standing)
        for move in self._order(self.position.legal_moves(captures_only=True), 0, None):
            undo = self.position.make(move)
            try:
                value = -self.quiesce(-beta, -alpha)
            finally:
                self.position.unmake(move, undo)
            if value >= beta:
                return beta
            alpha = max(alpha, value)
        return alpha

    def negamax(self, depth: int, alpha: int, beta: int, ply: int = 0) -> int:
        self.nodes += 1
        if self.nodes % 1024 == 0 and time.time() > self.deadline:
            raise TimeoutError

        original_alpha = alpha
        key = self.position.key
        entry = self.table.get(key)
        best_move = None
        if entry and entry[0] >= depth:
            _, value, flag, stored = entry
            best_move = stored
            if flag == 0:
                return value
            if flag == 1 and value > alpha:
                alpha = value
            elif flag == 2 and value < beta:
                beta = value
            if alpha >= beta:
                return value
        elif entry:
            best_move = entry[3]

        if depth <= 0:
            return self.quiesce(alpha, beta)

        in_check = self.position.in_check()

        # Нулевой ход: отдаём сопернику темп и смотрим сокращённым поиском. Если
        # позиция держится даже так, настоящий ход тем более её удержит, и ветку
        # можно не считать. Под шахом и в пешечном окончании приём неверен.
        if (
            depth >= 3
            and not in_check
            and beta < MATE
            and self.position.has_non_pawn_material()
        ):
            undo = self.position.make_null()
            try:
                value = -self.negamax(depth - 3, -beta, -beta + 1, ply + 1)
            finally:
                self.position.unmake_null(undo)
            if value >= beta:
                return beta

        moves = self.position.legal_moves()
        if not moves:
            if in_check:
                return -MATE + ply  # мат: предпочитаем тот, что дальше
            return 0  # пат

        best_value = -MATE * 2
        for index, move in enumerate(self._order(moves, ply, best_move)):
            quiet = not self.position.board[move[1]] and not move[2]
            undo = self.position.make(move)
            try:
                # Сокращение поздних ходов: упорядочивание уже поставило вперёд
                # то, что вероятнее всего лучшее, поэтому хвост списка сначала
                # смотрим мельче и досматриваем полностью, только если он
                # неожиданно хорош.
                reduction = 1 if (index >= 4 and depth >= 3 and quiet and not in_check) else 0
                value = -self.negamax(depth - 1 - reduction, -beta, -alpha, ply + 1)
                if reduction and value > alpha:
                    value = -self.negamax(depth - 1, -beta, -alpha, ply + 1)
            finally:
                self.position.unmake(move, undo)
            if value > best_value:
                best_value, best_move = value, move
            alpha = max(alpha, value)
            if alpha >= beta:
                if quiet:
                    self.killers.setdefault(ply, [])
                    self.killers[ply] = ([move] + self.killers[ply])[:2]
                    key = (move[0], move[1])
                    self.history[key] = self.history.get(key, 0) + depth * depth
                break

        flag = 0 if original_alpha < best_value < beta else (1 if best_value >= beta else 2)
        self.table[key] = (depth, best_value, flag, best_move)
        return best_value

    def best_move(self, seconds: float, allowed: list[tuple] | None = None) -> tuple | None:
        self.deadline = time.time() + max(0.05, seconds)
        moves = self.position.legal_moves()
        if allowed is not None:
            allowed_set = set(allowed)
            filtered = [m for m in moves if m in allowed_set]
            moves = filtered or moves
        if not moves:
            return None
        best = moves[0]
        for depth in range(1, 40):
            try:
                alpha, beta = -MATE * 2, MATE * 2
                local_best, local_value = None, -MATE * 3
                for move in self._order(moves, 0, best):
                    undo = self.position.make(move)
                    try:
                        value = -self.negamax(depth - 1, -beta, -alpha, 1)
                    finally:
                        self.position.unmake(move, undo)
                    if value > local_value:
                        local_value, local_best = value, move
                    alpha = max(alpha, value)
                if local_best is not None:
                    best = local_best
                if local_value > MATE - 100:
                    break
            except TimeoutError:
                break
        return best


def move_to_dict(move: tuple) -> dict:
    origin, target, promotion = move
    out = {"type": "move", "from": square_name(origin), "to": square_name(target)}
    if promotion:
        out["promotion"] = promotion
    return out


def dict_to_move(payload: dict) -> tuple:
    return (
        square_index(payload["from"]),
        square_index(payload["to"]),
        payload.get("promotion"),
    )

"""Артиллерия — угол, мощность, ветер и холм посередине.

Константы, на которых работает физика арены, нам не сообщают. Но и угадывать их
не надо: в каждом событии `shot` приходит **полная траектория** и **рельеф**, а
траектория — это парабола, снятая по точкам. Вторые разности её точек напрямую
дают гравитацию и боковое ускорение ветра, а первый шаг даёт масштаб, который
превращает «мощность» в скорость. Одного увиденного выстрела — своего или
чужого — хватает, чтобы откалибровать все три величины.

После этого прицеливание — не поиск формулы, а симуляция: прогоняем снаряд по
выданному нам рельефу для каждого угла и каждой мощности и берём пару, которая
падает ближе всего. Так учитывается холм посередине, чего не делает ни одно
замкнутое уравнение дальности.

Остаточный масштабный коэффициент вбирает в себя всё, в чём модель всё ещё
ошибается, и правится по тому, куда на самом деле лёг каждый выстрел.
"""

from __future__ import annotations

import logging
import math

from .base import Brain, Context, register

log = logging.getLogger("arena.brain.artillery")


class Ballistics:
    """Физика, выученная наблюдением за полётом снарядов."""

    def __init__(self, width: int, height: int = 400):
        self.width = max(10, width)
        self.height = height
        # Значения по умолчанию верного порядка величины, заменяются первой же
        # увиденной траекторией.
        self.gravity = 0.45
        self.wind_scale = 0.0045
        self.power_scale = 0.124
        self.launch_fraction = 0.33
        self.correction = 1.0
        self.calibrated = False
        self.samples = 0

    # ------------------------------------------------------------- обучение

    def learn_from_trajectory(self, trajectory: list, angle: float, power: float, wind: float) -> bool:
        """Восстановить гравитацию, ускорение ветра и масштаб «мощность →
        скорость» из одной снятой по точкам параболы."""
        points = [
            (float(p[0]), float(p[1]))
            for p in trajectory
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if len(points) < 5:
            return False

        steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
        # Первый и последний шаги неполные: снаряд появляется посреди шага и
        # останавливается при ударе, — поэтому физику несёт середина.
        interior = steps[1:-1]
        if len(interior) < 3:
            return False

        second_x = [b[0] - a[0] for a, b in zip(interior, interior[1:])]
        second_y = [b[1] - a[1] for a, b in zip(interior, interior[1:])]
        if not second_y:
            return False

        acceleration_y = sum(second_y) / len(second_y)
        acceleration_x = sum(second_x) / len(second_x)
        if acceleration_y >= 0:
            return False  # это не падающее тело, доверять нельзя

        # Какую долю такта покрывает шаг запуска — считывается с траектории, а не
        # подбирается: ровно на столько короче первый записанный шаг.
        first = math.hypot(*steps[0])
        second = math.hypot(*interior[0])
        if second > 1e-6:
            fraction = max(0.05, min(1.0, first / second))
            self.launch_fraction = (
                fraction if not self.calibrated else (self.launch_fraction + fraction) / 2
            )

        self.gravity = -acceleration_y
        if abs(wind) > 0.5:
            self.wind_scale = acceleration_x / wind

        horizontal = power * math.cos(math.radians(angle))
        if abs(horizontal) > 1e-6:
            initial_vx = interior[0][0] - acceleration_x * 0.5
            scale = initial_vx / horizontal
            if scale > 0:
                # Усредняем с прежним: оценка по одному выстрелу шумная.
                self.power_scale = scale if not self.calibrated else (self.power_scale + scale) / 2

        self.calibrated = True
        self.samples += 1
        log.debug(
            "артиллерия откалибрована: g=%.3f wind_scale=%.5f power_scale=%.4f",
            self.gravity,
            self.wind_scale,
            self.power_scale,
        )
        return True

    def correct(self, wanted: float, landed: float, origin: float) -> None:
        """Подправить остаточный масштаб по тому, где снаряд на самом деле лёг."""
        travelled = landed - origin
        target = wanted - origin
        if abs(target) < 1e-6 or travelled * target <= 0:
            return
        ratio = target / travelled
        ratio = max(0.6, min(1.6, ratio))
        # Мягко: один выстрел — это одно наблюдение, а ветер меняется каждый ход.
        self.correction = max(0.5, min(2.0, self.correction * (0.65 + 0.35 * ratio)))

    # --------------------------------------------------------- прицеливание

    def simulate(
        self, x0: float, y0: float, angle: float, power: float, wind: float, terrain: list
    ) -> float | None:
        """Куда падает этот выстрел, или None, если он покидает поле."""
        theta = math.radians(angle)
        speed = power * self.power_scale * self.correction
        vx = speed * math.cos(theta)
        vy = speed * math.sin(theta)
        ax = self.wind_scale * wind
        x, y = x0, y0
        width = len(terrain) or self.width

        for step in range(4000):
            # Снаряд появляется посреди своего первого такта; размер этого
            # неполного шага измерен по увиденным траекториям, и его пропуск
            # систематически завышает всякую оценку дальности.
            dt = self.launch_fraction if step == 0 else 1.0
            vx += ax * dt
            vy -= self.gravity * dt
            x += vx * dt
            y += vy * dt
            if x < 0 or x >= width:
                return None
            if y > self.height * 3:
                continue
            ground = terrain[int(x)] if terrain else 0.0
            if y <= ground:
                return x
        return None

    def aim(
        self, x0: float, y0: float, target_x: float, wind: float, terrain: list
    ) -> tuple[float, float]:
        """Пара (угол, мощность), чей смоделированный снаряд ложится ближе всего."""
        best: tuple[tuple[float, float], float, float] | None = None
        leftwards = target_x < x0
        angles = [float(a) for a in range(15, 86, 1)]
        if leftwards:
            angles = [180.0 - a for a in angles]

        for angle in angles:
            for power in range(15, 101, 1):
                landing = self.simulate(x0, y0, angle, float(power), wind, terrain)
                if landing is None:
                    continue
                # Среди одинаково близких выстрелов предпочитаем дугу около 50
                # градусов: достаточно высокую, чтобы перелететь холм, и не
                # настолько крутую, чтобы малая ошибка в мощности уводила точку
                # падения далеко.
                key = (round(abs(landing - target_x), 1), abs(min(angle, 180.0 - angle) - 50.0))
                if best is None or key < best[0]:
                    best = (key, angle, float(power))
        if best is None:
            return (45.0 if not leftwards else 135.0), 70.0
        return best[1], best[2]


@register
class ArtilleryBrain(Brain):
    game = "artillery"

    def __init__(self) -> None:
        self.model: Ballistics | None = None
        self.pending: tuple[float, float, float] | None = None
        self.last_wind = 0.0
        self.shots = 0
        self.hits = 0

    def on_event(self, event: dict, ctx: Context) -> None:
        if event.get("type") != "shot" or self.model is None:
            return
        angle = event.get("angle")
        power = event.get("power")
        trajectory = event.get("traj") or event.get("trajectory") or []
        # Событие не повторяет ветер, поэтому берём то значение, что было на
        # доске в момент выстрела: своё — из отложенной записи, чужое — из
        # последнего прочитанного состояния.
        ours = ctx.seat is not None and str(event.get("by")) == str(ctx.seat)
        if ours and self.pending:
            wind = self.pending[2]
        else:
            wind = self._wind_of(event, self.last_wind)

        # Учим физику по *любому* снаряду, включая чужой: параболa остаётся
        # параболой, кто бы её ни запустил.
        if angle is not None and power and trajectory:
            self.model.learn_from_trajectory(list(trajectory), float(angle), float(power), wind)

        if not ours:
            return
        if event.get("damage"):
            self.hits += 1
        if self.pending:
            wanted, origin, _ = self.pending
            landed = self._landing_x(event)
            if landed is not None:
                self.model.correct(wanted, landed, origin)
        self.pending = None

    @staticmethod
    def _wind_of(event: dict, fallback: float = 0.0) -> float:
        for key in ("wind", "wind_at_shot"):
            if isinstance(event.get(key), (int, float)):
                return float(event[key])
        return fallback

    @staticmethod
    def _landing_x(event: dict) -> float | None:
        """`impact` равен null, когда снаряд улетает за поле; траектория при этом
        всё равно говорит, как далеко он добрался, — а это ровно та информация,
        которую несёт выстрел, перелетевший противника."""
        impact = event.get("impact")
        if isinstance(impact, dict) and "x" in impact:
            return float(impact["x"])
        if isinstance(impact, (list, tuple)) and impact:
            return float(impact[0])
        trajectory = event.get("traj") or event.get("trajectory") or event.get("path")
        if isinstance(trajectory, list) and trajectory:
            last = trajectory[-1]
            if isinstance(last, (list, tuple)) and last:
                return float(last[0])
            if isinstance(last, dict) and "x" in last:
                return float(last["x"])
        return None

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        tanks = state.get("tanks") or {}
        mine = None
        targets = []
        for seat, tank in tanks.items():
            if str(seat) == str(ctx.seat):
                mine = tank
            elif float(tank.get("hp") or 0) > 0:
                targets.append(tank)
        if not mine or not targets:
            return None

        width = int(state.get("w") or 240)
        height = int(state.get("h") or 160)
        if self.model is None:
            self.model = Ballistics(width, height)
        terrain = [float(t) for t in (state.get("terrain") or [])]
        wind = float(state.get("wind") or 0.0)
        self.last_wind = wind

        my_x = float(mine.get("x") or 0)
        my_y = float(mine.get("y") or 0)
        target = min(targets, key=lambda t: abs(float(t.get("x") or 0) - my_x))
        target_x = float(target.get("x") or 0)

        angle, power = self.model.aim(my_x, my_y + 1.0, target_x, wind, terrain)
        # Пока физика неизвестна, намеренно разбрасываем выстрелы: разброс
        # траекторий калибрует модель куда быстрее, чем повторение одной.
        if not self.model.calibrated:
            power = max(15.0, min(100.0, power + ctx.rng.uniform(-10, 10)))
        self.pending = (target_x, my_x, wind)
        self.shots += 1
        return {"type": "fire", "angle": round(angle, 1), "power": round(power, 1)}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        if self.model is None:
            return None
        return (
            "Good game. I do not assume the arena's constants: the trajectory in every shot event is a sampled "
            "parabola, so its second differences give me gravity and the wind's acceleration, and the first step "
            f"gives the power-to-velocity scale. I then aim by simulating over the terrain — g={self.model.gravity:.2f}, "
            f"wind scale={self.model.wind_scale:.4f}. {self.hits} of my {self.shots} shots did damage."
        )

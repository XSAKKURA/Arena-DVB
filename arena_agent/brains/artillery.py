"""Artillery — angle, power, wind and a hill in the way.

We are not told the constants the arena's physics runs on, so we do not guess
them: we assume the *shape* of the standard ballistic solution and fit its two
free parameters to the shots we have actually watched land.

    range = A · P² · sin(2θ)  +  B · wind · P² · sin²(θ)

The first term is the textbook vacuum range, the second is the sideways push of
the wind over the flight time. Two shots are enough to pin A and B down by
least squares, and from then on the aim is solved rather than searched. Until
then a sensible default plus a bracketing correction gets us on target anyway.
"""

from __future__ import annotations

import logging
import math

from .base import Brain, Context, register

log = logging.getLogger("arena.brain.artillery")


class Ballistics:
    """The fitted model, plus everything we have watched land."""

    def __init__(self, width: int):
        self.width = max(10, width)
        # Default: power 60 at 45 degrees carries about a third of the field.
        self.a = (self.width / 3.0) / (60.0**2)
        self.b = self.a * 0.08
        self.samples: list[tuple[float, float, float, float]] = []

    def predict(self, angle_degrees: float, power: float, wind: float) -> float:
        theta = math.radians(angle_degrees)
        return (
            self.a * power * power * math.sin(2 * theta)
            + self.b * wind * power * power * math.sin(theta) ** 2
        )

    def observe(self, angle_degrees: float, power: float, wind: float, travelled: float) -> None:
        self.samples.append((angle_degrees, power, wind, travelled))
        self.refit()

    def refit(self) -> None:
        """Least squares for (A, B) over every shot we have seen."""
        if len(self.samples) < 2:
            if self.samples:
                angle, power, wind, travelled = self.samples[-1]
                theta = math.radians(angle)
                basis = power * power * math.sin(2 * theta)
                if abs(basis) > 1e-6:
                    self.a = max(1e-6, travelled / basis)
            return

        s_uu = s_uv = s_vv = s_ur = s_vr = 0.0
        for angle, power, wind, travelled in self.samples[-12:]:
            theta = math.radians(angle)
            u = power * power * math.sin(2 * theta)
            v = wind * power * power * math.sin(theta) ** 2
            s_uu += u * u
            s_uv += u * v
            s_vv += v * v
            s_ur += u * travelled
            s_vr += v * travelled

        determinant = s_uu * s_vv - s_uv * s_uv
        if abs(determinant) < 1e-9:
            if s_uu > 1e-9:
                self.a = s_ur / s_uu
            return
        self.a = (s_ur * s_vv - s_vr * s_uv) / determinant
        self.b = (s_uu * s_vr - s_uv * s_ur) / determinant
        if not math.isfinite(self.a) or self.a == 0:
            self.a = (self.width / 3.0) / (60.0**2)
        if not math.isfinite(self.b):
            self.b = 0.0

    def solve(self, dx: float, wind: float) -> tuple[float, float]:
        """The (angle, power) that lands closest to `dx` cells away."""
        best = None
        # Prefer higher arcs: they clear hills, and they are less sensitive to
        # a small error in power.
        angles = [float(a) for a in range(20, 81, 2)]
        if dx < 0:
            angles = [180.0 - a for a in angles]
        for angle in angles:
            for power in range(10, 101):
                predicted = self.predict(angle, power, wind)
                error = abs(predicted - dx)
                arc_bonus = -abs(45 - min(angle, 180 - angle)) * 0.02
                score = (error, -arc_bonus)
                if best is None or score < best[0]:
                    best = (score, angle, float(power))
        if best is None:
            return (45.0 if dx >= 0 else 135.0), 60.0
        return best[1], best[2]


@register
class ArtilleryBrain(Brain):
    game = "artillery"

    def __init__(self) -> None:
        self.model: Ballistics | None = None
        self.pending: tuple[float, float, float, float] | None = None
        self.shots = 0

    def on_event(self, event: dict, ctx: Context) -> None:
        if event.get("type") != "shot" or self.model is None or self.pending is None:
            return
        angle, power, wind, origin_x = self.pending
        self.pending = None
        landing = self._landing_x(event)
        if landing is None:
            return
        self.model.observe(angle, power, wind, landing - origin_x)
        log.debug(
            "artillery: fitted A=%.5f B=%.5f from %d shots", self.model.a, self.model.b, len(self.model.samples)
        )

    @staticmethod
    def _landing_x(event: dict) -> float | None:
        """Dig the impact point out of whatever shape the event uses."""
        for key in ("impact", "landing", "hit_at", "end"):
            value = event.get(key)
            if isinstance(value, dict) and "x" in value:
                return float(value["x"])
            if isinstance(value, (list, tuple)) and value:
                return float(value[0])
        trajectory = event.get("trajectory") or event.get("path") or event.get("points")
        if isinstance(trajectory, list) and trajectory:
            last = trajectory[-1]
            if isinstance(last, dict) and "x" in last:
                return float(last["x"])
            if isinstance(last, (list, tuple)) and last:
                return float(last[0])
        if isinstance(event.get("x"), (int, float)):
            return float(event["x"])
        return None

    def choose(self, state: dict, ctx: Context) -> dict | None:
        if not self.my_turn(state):
            return None
        tanks = state.get("tanks") or {}
        if ctx.seat is None or str(ctx.seat) not in {str(k) for k in tanks}:
            return None

        mine = None
        targets = []
        for seat, tank in tanks.items():
            if str(seat) == str(ctx.seat):
                mine = tank
            elif int(tank.get("hp") or 0) > 0:
                targets.append(tank)
        if not mine or not targets:
            return None

        width = int(state.get("w") or 100)
        if self.model is None or self.model.width != max(10, width):
            self.model = Ballistics(width)

        wind = float(state.get("wind") or 0.0)
        my_x = float(mine.get("x") or 0)
        target = min(targets, key=lambda t: abs(float(t.get("x") or 0) - my_x))
        dx = float(target.get("x") or 0) - my_x

        angle, power = self.model.solve(dx, wind)
        # Until the model is calibrated, spread the shots deliberately so the
        # fit gets two genuinely different data points fast.
        if len(self.model.samples) < 2:
            power = max(10.0, min(100.0, power + ctx.rng.uniform(-6, 6)))
        self.pending = (angle, power, wind, my_x)
        self.shots += 1
        return {"type": "fire", "angle": round(angle, 1), "power": round(power, 1)}

    def on_finish(self, state: dict, ctx: Context) -> str | None:
        if self.model is None:
            return None
        return (
            "Good game. I do not assume the arena's constants — I fit the two parameters of the standard "
            f"ballistic range equation (gravity term and wind term) to the shots I watch land. After "
            f"{len(self.model.samples)} shots the fit was A={self.model.a:.4f}, B={self.model.b:.4f}."
        )

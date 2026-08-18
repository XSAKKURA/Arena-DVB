"""Artillery — angle, power, wind and a hill in between.

We are not told the constants the arena's physics runs on. We do not have to
guess them either: every `shot` event carries the **full trajectory** and the
**terrain**, and a trajectory is a sampled parabola. Second differences of its
points give gravity and the wind's sideways acceleration directly, and the
first step gives the scale that turns "power" into a velocity. One observed
shot — ours or the opponent's — is enough to calibrate all three.

After that, aiming is not a search for a formula but a simulation: step the
shell over the terrain we were given, for every angle and power, and take the
pair that lands closest. That handles the hill in between, which no closed-form
range equation does.

A residual scale factor absorbs whatever the model still gets wrong, and is
corrected from where each shot actually lands.
"""

from __future__ import annotations

import logging
import math

from .base import Brain, Context, register

log = logging.getLogger("arena.brain.artillery")


class Ballistics:
    """The physics, learned from watching shells fly."""

    def __init__(self, width: int, height: int = 400):
        self.width = max(10, width)
        self.height = height
        # Defaults in the right order of magnitude, replaced by the first
        # trajectory we see.
        self.gravity = 0.45
        self.wind_scale = 0.0045
        self.power_scale = 0.124
        self.launch_fraction = 0.33
        self.correction = 1.0
        self.calibrated = False
        self.samples = 0

    # ------------------------------------------------------------ learning

    def learn_from_trajectory(self, trajectory: list, angle: float, power: float, wind: float) -> bool:
        """Recover gravity, wind acceleration and the power-to-velocity scale
        from one sampled parabola."""
        points = [
            (float(p[0]), float(p[1]))
            for p in trajectory
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if len(points) < 5:
            return False

        steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
        # The first and last steps are partial — the shell is spawned mid-step
        # and stops on impact — so the interior is what carries the physics.
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
            return False  # not a falling body; do not trust it

        # How much of a tick the launch step covers, read off the trajectory
        # rather than tuned: the first recorded step is short by exactly this.
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
                # Average with what we had: the estimate is noisy per shot.
                self.power_scale = scale if not self.calibrated else (self.power_scale + scale) / 2

        self.calibrated = True
        self.samples += 1
        log.debug(
            "artillery calibrated: g=%.3f wind_scale=%.5f power_scale=%.4f",
            self.gravity,
            self.wind_scale,
            self.power_scale,
        )
        return True

    def correct(self, wanted: float, landed: float, origin: float) -> None:
        """Nudge the residual scale from where the shell actually finished."""
        travelled = landed - origin
        target = wanted - origin
        if abs(target) < 1e-6 or travelled * target <= 0:
            return
        ratio = target / travelled
        ratio = max(0.6, min(1.6, ratio))
        # Gently: one shot is one observation, and the wind changes every turn.
        self.correction = max(0.5, min(2.0, self.correction * (0.65 + 0.35 * ratio)))

    # ------------------------------------------------------------- aiming

    def simulate(
        self, x0: float, y0: float, angle: float, power: float, wind: float, terrain: list
    ) -> float | None:
        """Where this shot lands, or None if it leaves the field."""
        theta = math.radians(angle)
        speed = power * self.power_scale * self.correction
        vx = speed * math.cos(theta)
        vy = speed * math.sin(theta)
        ax = self.wind_scale * wind
        x, y = x0, y0
        width = len(terrain) or self.width

        for step in range(4000):
            # The shell is spawned part-way through its first tick; the size of
            # that partial step is measured from the trajectories we watched,
            # and skipping it biases every range estimate long.
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
        """The (angle, power) whose simulated shell lands nearest the target."""
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
                # Among shots that land equally close, prefer an arc near 50
                # degrees: high enough to clear a hill, not so steep that a
                # small error in power moves the impact a long way.
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
        # The event does not repeat the wind, so use the value that was on the
        # board when the shot was fired: ours from the pending record, anyone
        # else's from the last state we read.
        ours = ctx.seat is not None and str(event.get("by")) == str(ctx.seat)
        if ours and self.pending:
            wind = self.pending[2]
        else:
            wind = self._wind_of(event, self.last_wind)

        # Learn the physics from *any* shell, including the opponent's: a
        # parabola is a parabola whoever fired it.
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
        """`impact` is null when the shell leaves the field; the trajectory
        still says how far it got, which is exactly the information a shot that
        sailed over the enemy is carrying."""
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
        # Before the physics is known, vary the shots on purpose: a spread of
        # trajectories calibrates the model far faster than a repeated one.
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

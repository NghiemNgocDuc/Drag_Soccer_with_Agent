"""Small geometric support plan; no physics worlds, randomness or stored roles."""
from __future__ import annotations

import math

from models.soccer_logic import FIELD_W, FIELD_H, _MARGIN


def distance(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def path_clear(start, target, obstacles, radius):
    """Check the swept disc, allowing an already-touching disc to move away."""
    dx, dy = target['x'] - start['x'], target['y'] - start['y']
    length2 = dx * dx + dy * dy
    if length2 < 1e-9:
        return True
    for other in obstacles:
        ox, oy = other['x'] - start['x'], other['y'] - start['y']
        t = max(0.0, min(1.0, (ox * dx + oy * dy) / length2))
        if t == 0.0 and distance(target, other) > math.hypot(ox, oy):
            continue
        if (ox - t * dx) ** 2 + (oy - t * dy) ** 2 < (radius + other['radius'] + 6.0) ** 2:
            return False
    return True


class SupportPlan:
    """Keep a ball-side player and cover, then open distinct forward outlets."""
    def __init__(self, state, side, material, profiles):
        self.side, self.material, self.profiles = side, material, profiles
        self.own = state['players_a' if side else 'players_b']
        self.opponents = state['players_b' if side else 'players_a']
        self.ball = state['ball']
        self.direction = 1 if side else -1
        self.carrier = min(range(len(self.own)), key=lambda i: (distance(self.own[i], self.ball), i))
        nearest = distance(self.own[self.carrier], self.ball)
        pressure = min((distance(p, self.ball) for p in self.opponents), default=FIELD_W)
        progress = self.ball['x'] if side else FIELD_W - self.ball['x']
        self.active = (len(self.own) >= 3 and not state.get('penalty_shootout')
                       and progress > 280 and nearest <= 180 and pressure >= nearest + 20)
        cover = [i for i in range(1, len(self.own)) if i != self.carrier]
        self.cover = min(cover, key=lambda i: (self.direction * self.own[i]['x'], i)) if len(self.own) >= 4 and cover else None
        self.circles = {
            team: [{**p, 'radius': profile[0] - material['radius']}
                   for p, profile in zip(state['players_a' if team else 'players_b'], profiles[team])]
            for team in (True, False)
        }
        self.before = {i: self.quality(i, p, self.own) for i, p in enumerate(self.own)} if self.active else {}

    def quality(self, index, point, roster):
        if index == 0 or index == self.carrier:
            return 0.0
        forward = self.direction * (point['x'] - self.ball['x'])
        spacing = min((distance(point, p) for i, p in enumerate(roster) if i != index), default=200)
        crowding = max(0.0, 120.0 - spacing) * 0.65
        if index == self.cover:
            # A defender follows the play but remains behind the ball.
            return (45.0 - min(160.0, abs(forward + 160) * .35)
                    - abs(point['y'] - self.ball['y']) * .08 - crowding)
        gap = distance(point, self.ball)
        if gap < 100 or gap > 460 or forward < 35:
            return -crowding
        obstacles = self.circles[not self.side] + [
            {**p, 'radius': self.circles[self.side][i]['radius']}
            for i, p in enumerate(roster) if i not in (index, self.carrier)
        ]
        if not path_clear(self.ball, point, obstacles, self.material['radius']):
            return -crowding
        pressure = min((distance(point, p) - p['radius'] for p in self.circles[not self.side]), default=200)
        freedom = max(0.0, min(1.0, (pressure - 35) / 100))
        separation = max(0.0, min(1.0, (spacing - 70) / 80))
        reach = min(1.0, (460 - gap) / 140)
        advance = min(1.0, forward / 160)
        return 150.0 * freedom * separation * reach * advance - crowding

    def gain(self, index, roster):
        if not self.active or index >= len(roster):
            return 0.0
        return self.quality(index, roster[index], roster) - self.before[index]

    def moves(self, allowed, cap):
        """At most twelve slots per outfielder; yield at most two useful runs each."""
        if not self.active:
            return
        for index in allowed:
            if index in (0, self.carrier):
                continue
            player = self.own[index]
            contact, braking, velocity, _ = self.profiles[self.side][index]
            radius = contact - self.material['radius']
            reach = (cap * velocity) ** 2 / (2 * braking)
            offsets = ((-160, y) for y in (0, -120, 120)) if index == self.cover else (
                (x, y) for x in (140, 250, 340) for y in (-170, 170, -250, 250))
            obstacles = (self.circles[not self.side]
                         + [p for i, p in enumerate(self.circles[self.side]) if i != index]
                         + [{**self.ball, 'radius': self.material['radius']}])
            ranked = []
            for forward, across in offsets:
                target = {
                    'x': max(_MARGIN + radius + 12, min(FIELD_W - _MARGIN - radius - 12, self.ball['x'] + self.direction * forward)),
                    'y': max(_MARGIN + radius + 12, min(FIELD_H - _MARGIN - radius - 12, self.ball['y'] + across)),
                }
                travel = distance(player, target)
                if travel < 24 or reach < 24:
                    continue
                fraction = min(1.0, reach / travel)
                target = {axis: player[axis] + (target[axis] - player[axis]) * fraction for axis in ('x', 'y')}
                if not path_clear(player, target, obstacles, radius):
                    continue
                roster = list(self.own)
                roster[index] = target
                gain = self.gain(index, roster)
                if gain < 8:
                    continue
                angle = math.degrees(math.atan2(target['y'] - player['y'], target['x'] - player['x'])) % 360
                power = min(cap, math.sqrt(2 * braking * distance(player, target)) / velocity)
                ranked.append((round(gain, 8), len(ranked), (index, angle, power)))
            ranked.sort(key=lambda entry: (-entry[0], entry[1]))
            yield from (entry[2] for entry in ranked[:2])

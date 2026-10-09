"""Physical invariants and API parity for the ground soccer simulation."""
import copy
import math

import pymunk
import pytest

from models import soccer_logic as physics


def _clear_shot_state(count=3, side_a=True):
    state = physics.new_soccer_state(player_count=count, half_length=9999)
    state["ball"].update(x=700.0, y=180.0)
    team = state["players_a"] if side_a else state["players_b"]
    team[-1].update(x=663.0 if side_a else 737.0, y=180.0)
    return state


def _circle(body):
    return next(shape for shape in body.shapes if isinstance(shape, pymunk.Circle))


@pytest.mark.parametrize("side", ["left", "right"])
def test_goal_requires_whole_ball_beyond_goal_line(side):
    radius = float(physics.BALL_R)
    line = float(physics._MARGIN if side == "left" else physics.FIELD_W - physics._MARGIN)
    direction = -1.0 if side == "left" else 1.0
    center_y = (physics.GOAL_Y1 + physics.GOAL_Y2) / 2
    # A leading edge or even the center crossing is insufficient.
    for offset in (-radius / 2, radius / 2, radius - 0.1):
        position = pymunk.Vec2d(line + direction * offset, center_y)
        assert physics._goal_for_ball(position, radius) is None
    position = pymunk.Vec2d(line + direction * (radius + 0.1), center_y)
    assert physics._goal_for_ball(position, radius).upper() == ("B" if side == "left" else "A")


@pytest.mark.parametrize("radius", [8.0, 12.0, 16.0])
@pytest.mark.parametrize("side", ["left", "right"])
def test_goal_ball_must_fit_between_both_posts(radius, side):
    x = physics._MARGIN - radius - 1 if side == "left" else physics.FIELD_W - physics._MARGIN + radius + 1
    outside_mouth = [physics.GOAL_Y1 + radius - 0.1, physics.GOAL_Y2 - radius + 0.1]
    for y in outside_mouth:
        assert physics._goal_for_ball(pymunk.Vec2d(x, y), radius) is None
    inside_mouth = [physics.GOAL_Y1 + radius + 0.1, physics.GOAL_Y2 - radius - 0.1]
    for y in inside_mouth:
        assert physics._goal_for_ball(pymunk.Vec2d(x, y), radius).upper() == ("B" if side == "left" else "A")


@pytest.mark.parametrize("side", ["left", "right"])
def test_goal_rejects_ball_above_the_crossbar(side):
    radius = float(physics.BALL_R)
    x = physics._MARGIN - radius - 1 if side == "left" else physics.FIELD_W - physics._MARGIN + radius + 1
    position = pymunk.Vec2d(x, physics.FIELD_H / 2)
    crossbar_height = physics._GOAL_DEPTH * 1.2
    assert physics._goal_for_ball(position, radius, z=0) is not None
    assert physics._goal_for_ball(position, radius, z=crossbar_height - 2 * radius + 0.1) is None


def test_ball_size_changes_collision_clearance_and_whole_goal_threshold():
    radii, minimum_y = {}, {}
    for size in ("small", "normal", "large"):
        state = physics.new_soccer_state()
        state["ball_size"] = size
        state["ball"].update(x=500.0, y=50.0)
        space, a, b, ball, referee, pivot = physics._build_space(state)
        radii[size] = _circle(ball).radius
        assert radii[size] == pytest.approx(physics._ball_physics(state)["radius"])
        ball.velocity = (0.0, -200.0)
        trajectory, _, _ = physics._sim(space, a, b, ball, referee, 0, True, ball_pivot=pivot)
        minimum_y[size] = min(point["y"] for point in trajectory)
    assert radii["small"] < radii["normal"] < radii["large"]
    assert minimum_y["large"] > minimum_y["small"] + (radii["large"] - radii["small"]) / 2
    position = pymunk.Vec2d(physics.FIELD_W - physics._MARGIN + radii["normal"] + 0.1,
                            physics.FIELD_H / 2)
    assert physics._goal_for_ball(position, radii["normal"]) == "A"
    assert physics._goal_for_ball(position, radii["large"]) is None


def test_ball_rolling_stops_without_reversing_and_matches_constant_deceleration():
    state = physics.new_soccer_state()
    state["ball"].update(x=500.0, y=180.0)
    space, _, _, ball, _, _ = physics._build_space(state)
    initial_speed = 180.0
    initial_x = ball.position.x
    ball.velocity = (initial_speed, 0.0)
    velocities = []
    for _ in range(600):
        space.step(physics._PM_DT / 3)
        velocities.append(ball.velocity.x)
    assert min(velocities) >= -1e-6
    assert all(v1 <= v0 + 1e-6 for v0, v1 in zip(velocities, velocities[1:]))
    assert ball.velocity.length < 2.0
    expected_distance = initial_speed ** 2 / (2 * physics._PM_LINEAR_FRICTION_B)
    assert ball.position.x - initial_x == pytest.approx(expected_distance, abs=3.0)


def test_wall_contact_loses_normal_kinetic_energy():
    state = physics.new_soccer_state()
    state["ball"].update(x=500.0, y=80.0)
    space, _, _, ball, _, _ = physics._build_space(state)
    ball.velocity = (0.0, -600.0)
    previous_speed = abs(ball.velocity.y)
    for _ in range(90):
        space.step(physics._PM_DT / 3)
        if ball.velocity.y > 0:
            incoming_energy = 0.5 * ball.mass * previous_speed ** 2
            outgoing_energy = 0.5 * ball.mass * ball.velocity.y ** 2
            # This excludes perfectly elastic contacts with only the tiny
            # friction loss incurred during a single physics substep.
            assert outgoing_energy < 0.95 * incoming_energy
            return
        previous_speed = abs(ball.velocity.y)
    pytest.fail("The ball never rebounded from the wall")


def test_ball_player_contact_loses_energy_without_adding_momentum():
    state = physics.new_soccer_state()
    state["ball"].update(x=500.0, y=180.0)
    state["players_b"][-1].update(x=560.0, y=180.0)
    space, _, b, ball, _, _ = physics._build_space(state)
    receiver = b[-1]
    ball.velocity = (600.0, 0.0)
    for _ in range(90):
        incoming_energy = 0.5 * ball.mass * ball.velocity.length_squared
        incoming_momentum = ball.mass * ball.velocity.x
        space.step(physics._PM_DT / 3)
        if receiver.velocity.x > 0.1:
            outgoing_energy = (0.5 * ball.mass * ball.velocity.length_squared +
                               0.5 * receiver.mass * receiver.velocity.length_squared)
            outgoing_momentum = ball.mass * ball.velocity.x + receiver.mass * receiver.velocity.x
            assert outgoing_energy < 0.95 * incoming_energy
            assert 0 < outgoing_momentum <= incoming_momentum + 1e-6
            return
    pytest.fail("The ball never struck the receiving player")


@pytest.mark.parametrize("speed", [100.0, 200.0])
def test_slow_wall_collision_is_recorded_as_an_actual_bounce(speed):
    state = physics.new_soccer_state()
    state["ball"].update(x=500.0, y=50.0)
    space, a, b, ball, referee, pivot = physics._build_space(state)
    ball.velocity = (0.0, -speed)
    trajectory, _, _ = physics._sim(space, a, b, ball, referee, 0, True, ball_pivot=pivot)
    assert any(point.get("bounce") for point in trajectory)
    assert any(p1["y"] > p0["y"] for p0, p1 in zip(trajectory, trajectory[1:]))
    assert all(isinstance(point["b"], list) for point in trajectory)


def test_stationary_ball_touching_wall_does_not_generate_impact():
    state = physics.new_soccer_state()
    state["ball"].update(x=500.0, y=physics._MARGIN + 5.0 + physics.BALL_R)
    space, a, b, ball, referee, pivot = physics._build_space(state)
    trajectory, _, _ = physics._sim(space, a, b, ball, referee, 0, True, ball_pivot=pivot)
    assert not any(point.get("bounce") for point in trajectory)


@pytest.mark.parametrize("side_a", [True, False])
def test_zero_power_leaves_ball_and_players_still(side_a):
    state = _clear_shot_state(side_a=side_a)
    before = copy.deepcopy(state)
    trajectory, goal, *_ = physics.apply_kick(state, 2, 0 if side_a else 180, 0, side_a)
    assert goal is None
    assert state["ball"] == before["ball"]
    for team in ("players_a", "players_b"):
        assert len(state[team]) == len(before[team])
        for actual, initial in zip(state[team], before[team]):
            assert (actual["x"], actual["y"]) == pytest.approx((initial["x"], initial["y"]), abs=1e-6)
    assert all(point.get("z", 0) == 0 for point in trajectory)


@pytest.mark.parametrize("execute", [physics.simulate_kick, physics.apply_kick, physics.apply_penalty_kick])
@pytest.mark.parametrize("field", ["angle", "power"])
@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_nonfinite_kick_commands_are_rejected_without_state_mutation(execute, field, invalid):
    state = _clear_shot_state()
    before = copy.deepcopy(state)
    angle, power = (invalid, 80.0) if field == "angle" else (0.0, invalid)
    with pytest.raises(ValueError):
        execute(state, 2, angle, power, True)
    assert state == before


def test_prediction_and_actual_play_enforce_saved_power_cap():
    state = _clear_shot_state()
    state["power_cap"] = 60
    expected, expected_goal = physics.simulate_kick(state, 2, 0, 60, True)
    capped, capped_goal = physics.simulate_kick(state, 2, 0, 1e9, True)
    played, played_goal, *_ = physics.apply_kick(copy.deepcopy(state), 2, 0, 1e9, True)
    assert capped == expected == played
    assert capped_goal == expected_goal == played_goal
    zero, zero_goal = physics.simulate_kick(state, 2, 0, 0, True)
    negative, negative_goal = physics.simulate_kick(state, 2, 0, -100, True)
    assert negative == zero
    assert negative_goal == zero_goal


@pytest.mark.parametrize("side", ["left", "right"])
def test_regular_simulation_does_not_score_a_stationary_partial_crossing(side):
    state = physics.new_soccer_state()
    line = physics._MARGIN if side == "left" else physics.FIELD_W - physics._MARGIN
    direction = -1 if side == "left" else 1
    state["ball"].update(x=line + direction * (physics.BALL_R / 2), y=physics.FIELD_H / 2)
    space, a, b, ball, referee, pivot = physics._build_space(state)
    _, scored, _ = physics._sim(space, a, b, ball, referee, 0, True, max_steps=1, ball_pivot=pivot)
    assert scored is None


@pytest.mark.parametrize("side_a", [True, False])
def test_penalty_simulation_does_not_score_when_only_leading_edge_crossed(side_a):
    state = physics.new_soccer_state()
    space, kicker, ball, keeper = physics._build_penalty_space(state, side_a)
    line = physics.FIELD_W - physics._MARGIN if side_a else physics._MARGIN
    direction = 1 if side_a else -1
    ball.position = (line - direction * (physics.BALL_R - 1), physics.FIELD_H / 2)
    _, scored = physics._sim_penalty(space, kicker, ball, keeper, "center", max_steps=1)
    assert not scored


@pytest.mark.parametrize("side_a", [True, False])
def test_penalty_wrong_goal_crossing_never_counts_for_the_shooter(side_a):
    state = physics.new_soccer_state()
    space, kicker, ball, keeper = physics._build_penalty_space(state, side_a)
    ball.position = (40.0 if side_a else 1360.0, physics.FIELD_H / 2)
    ball.velocity = (-10000.0 if side_a else 10000.0, 0.0)
    trajectory, scored = physics._sim_penalty(space, kicker, ball, keeper, "center", max_steps=1)
    assert physics._goal_for_ball(pymunk.Vec2d(trajectory[-1]["x"], trajectory[-1]["y"]),
                                  _circle(ball).radius) == ("B" if side_a else "A")
    assert not scored


@pytest.mark.parametrize("side_a", [True, False])
def test_high_speed_goal_is_detected_within_a_single_physics_substep(side_a):
    state = physics.new_soccer_state()
    state["ball"].update(x=1360.0 if side_a else 40.0, y=physics.FIELD_H / 2)
    space, a, b, ball, referee, pivot = physics._build_space(state)
    # This crosses the line and reaches the net before one 60 Hz replay frame.
    ball.velocity = (10000.0 if side_a else -10000.0, 0.0)
    trajectory, scored, _ = physics._sim(space, a, b, ball, referee, 0, side_a, max_steps=1, ball_pivot=pivot)
    assert scored == ("A" if side_a else "B")
    assert 0 < trajectory[-1]["t"] < physics._PM_DT
    assert physics._goal_for_ball(pymunk.Vec2d(trajectory[-1]["x"], trajectory[-1]["y"]),
                                  _circle(ball).radius) == scored


@pytest.mark.parametrize("side_a", [True, False])
def test_missed_ball_prediction_matches_play_and_retains_moving_pawn(side_a):
    state = _clear_shot_state(side_a=side_a)
    team = "players_a" if side_a else "players_b"
    state[team][-1].update(x=500.0 if side_a else 900.0, y=180.0)
    before = copy.deepcopy(state)
    args = (2, 180 if side_a else 0, 50.0, side_a)
    predicted, prediction_goal = physics.simulate_kick(state, *args)
    assert state == before
    played, actual_goal, *_ = physics.apply_kick(state, *args)
    assert predicted == played
    assert prediction_goal == actual_goal is None
    assert len(predicted) > 1
    assert all(point["x"] == before["ball"]["x"] and point["y"] == before["ball"]["y"] for point in predicted)
    trajectory_team = "a" if side_a else "b"
    assert predicted[-1][trajectory_team][-1]["x"] != before[team][-1]["x"]


@pytest.mark.parametrize("count", [3, 7, 11])
@pytest.mark.parametrize("side_a", [True, False])
@pytest.mark.parametrize("stats", [
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
    {"size": 20, "power": 80, "weight": 20, "agility": 80},
    {"size": 80, "power": 20, "weight": 80, "agility": 20},
])
def test_ai_prediction_is_identical_to_authoritative_play_for_all_builds(count, side_a, stats):
    state = _clear_shot_state(count, side_a)
    physics.inject_player_stats(state, [stats] * count, [stats] * count)
    before = copy.deepcopy(state)
    args = (count - 1, 0 if side_a else 180, 85, side_a)
    predicted, prediction_goal = physics.simulate_kick(state, *args)
    assert state == before
    played, actual_goal, *_ = physics.apply_kick(state, *args)
    assert predicted == played
    assert prediction_goal == actual_goal
    assert all(point.get("z", 0) == 0 for point in played)


@pytest.mark.parametrize("side_a", [True, False])
def test_penalty_setup_and_next_kick_preserve_every_players_stats(side_a):
    state = physics.new_soccer_state(player_count=3)
    stats_a = [
        {"size": 80, "power": 20, "weight": 80, "agility": 20},
        {"size": 50, "power": 50, "weight": 50, "agility": 50},
        {"size": 20, "power": 80, "weight": 20, "agility": 80},
    ]
    stats_b = list(reversed(stats_a))
    physics.inject_player_stats(state, stats_a, stats_b)
    physics._setup_penalty_positions(state, side_a)
    assert [player["stats"] for player in state["players_a"]] == stats_a
    assert [player["stats"] for player in state["players_b"]] == stats_b
    physics.apply_penalty_kick(state, 2, 0 if side_a else 180, 0, side_a)
    assert [player["stats"] for player in state["players_a"]] == stats_a
    assert [player["stats"] for player in state["players_b"]] == stats_b


@pytest.mark.parametrize("side_a", [True, False])
@pytest.mark.parametrize("keeper_style", ["default", "deflector", "footwork_plus"])
@pytest.mark.parametrize("selected_stats", [
    {"size": 50, "power": 50, "weight": 50, "agility": 50},
    {"size": 20, "power": 80, "weight": 20, "agility": 80},
    {"size": 80, "power": 20, "weight": 80, "agility": 20},
])
def test_penalty_ai_forecast_matches_play_for_selected_build_and_keeper(side_a, keeper_style, selected_stats):
    state = physics.new_soccer_state()
    state["penalty_shootout"] = True
    state["period"] = "penalties"
    defaults = dict(physics.DEFAULT_STATS)
    team_stats = [defaults, defaults, selected_stats]
    physics.inject_player_stats(state, team_stats, team_stats)
    state["keeper_style_b" if side_a else "keeper_style_a"] = keeper_style
    physics._setup_penalty_positions(state, side_a)
    state["penalty_goalkeeper_move"] = "left"
    before = copy.deepcopy(state)
    args = (2, -8 if side_a else 188, 100, side_a)
    predicted, prediction_goal = physics.simulate_kick(state, *args)
    assert state == before
    played, scored, _ = physics.apply_penalty_kick(state, *args)
    assert predicted == played
    assert prediction_goal == (("A" if side_a else "B") if scored else None)


@pytest.mark.parametrize("side_a", [True, False])
def test_penalty_kicker_mass_and_radius_come_from_selected_players_stats(side_a):
    state = physics.new_soccer_state()
    stats = [
        {"size": 80, "power": 20, "weight": 80, "agility": 20},
        {"size": 50, "power": 50, "weight": 50, "agility": 50},
        {"size": 20, "power": 80, "weight": 20, "agility": 80},
    ]
    physics.inject_player_stats(state, stats, stats)
    physics._setup_penalty_positions(state, side_a)
    _, kicker, _, _ = physics._build_penalty_space(state, side_a, player_idx=2)
    assert kicker.mass == pytest.approx(physics._get_player_mass(stats[2]))
    assert _circle(kicker).radius == pytest.approx(physics._get_player_radius(stats[2]))
    assert kicker.mass != physics._get_player_mass(stats[0])


@pytest.mark.parametrize("side_a", [True, False])
def test_deflector_cannot_teleport_ball_when_keeper_makes_no_contact(side_a):
    state = physics.new_soccer_state()
    state["keeper_style_b" if side_a else "keeper_style_a"] = "deflector"
    physics._setup_penalty_positions(state, side_a)
    initial_ball = copy.deepcopy(state["ball"])
    trajectory, scored, _ = physics.apply_penalty_kick(state, 0, 0 if side_a else 180, 0, side_a)
    assert not scored
    assert all(point["x"] == initial_ball["x"] and point["y"] == initial_ball["y"]
               for point in trajectory)


@pytest.mark.parametrize("ball_type", ["normal", "beach", "puck"])
def test_regular_and_penalty_ball_use_identical_material_properties(ball_type):
    state = physics.new_soccer_state()
    state["ball_type"] = ball_type
    _, _, _, regular_ball, _, _ = physics._build_space(state)
    _, _, penalty_ball, _ = physics._build_penalty_space(state, True)
    regular_shape, penalty_shape = _circle(regular_ball), _circle(penalty_ball)
    assert regular_ball.mass == pytest.approx(penalty_ball.mass)
    assert regular_shape.radius == pytest.approx(penalty_shape.radius)
    assert regular_shape.elasticity == pytest.approx(penalty_shape.elasticity)
    assert regular_shape.friction == pytest.approx(penalty_shape.friction)


@pytest.mark.parametrize("ball_type", ["normal", "beach", "puck"])
def test_selected_ball_material_controls_body_mass_radius_and_stopping_distance(ball_type):
    state = physics.new_soccer_state()
    state["ball_type"] = ball_type
    state["ball"].update(x=500.0, y=180.0)
    material = physics._ball_physics(state)
    space, a, b, ball, referee, pivot = physics._build_space(state)
    assert ball.mass == pytest.approx(material["mass"])
    assert _circle(ball).radius == pytest.approx(material["radius"])
    speed = 120.0
    start_x = ball.position.x
    ball.velocity = (speed, 0.0)
    physics._sim(space, a, b, ball, referee, 0, True, ball_pivot=pivot)
    assert ball.velocity.length < 0.5
    expected = speed ** 2 / (2 * material["rolling_deceleration"])
    assert ball.position.x - start_x == pytest.approx(expected, abs=1.5)


def test_nonstandard_balls_have_distinct_physical_properties():
    normal = physics._ball_physics({"ball_type": "normal"})
    beach = physics._ball_physics({"ball_type": "beach"})
    puck = physics._ball_physics({"ball_type": "puck"})
    assert beach["mass"] < normal["mass"]
    assert beach["rolling_deceleration"] < normal["rolling_deceleration"]
    assert puck["rolling_deceleration"] < normal["rolling_deceleration"]


def _settle_into_sleep(space, players):
    for _ in range(60):
        space.step(physics._PM_DT / 3)
    assert all(body.is_sleeping for body in players)


def test_delayed_ball_contact_wakes_sleeping_receiver_and_updates_replay_positions():
    state = physics.new_soccer_state()
    state["ball"].update(x=450.0, y=180.0)
    state["players_b"][-1].update(x=560.0, y=180.0)
    space, a, b, ball, referee, pivot = physics._build_space(state)
    receiver = b[-1]
    _settle_into_sleep(space, a + b)
    start_x = receiver.position.x
    observed_wakes = []

    def contact(arbiter, _space, _data):
        if any(shape.body is receiver for shape in arbiter.shapes):
            observed_wakes.append(not receiver.is_sleeping)

    physics._on_collision(space, physics._CAT_BALL, physics._CAT_PLAYER, post_solve=contact)
    ball.velocity = (600.0, 0.0)
    assert receiver.is_sleeping
    trajectory, _, _ = physics._sim(space, a, b, ball, referee, 0, True, ball_pivot=pivot)
    assert observed_wakes and all(observed_wakes)
    assert receiver.position.x > start_x + 1
    stationary = [point for point in trajectory if point["b"][-1]["x"] == round(start_x, 1)]
    assert any(point["t"] > 0.1 for point in stationary)
    assert trajectory[-1]["b"][-1]["x"] == round(receiver.position.x, 1)
    assert trajectory[-1]["b"][-1]["x"] > trajectory[0]["b"][-1]["x"]


def test_launch_impulse_wakes_sleeping_kicker_and_moves_recorded_pawn():
    state = _clear_shot_state()
    space, a, b, ball, referee, pivot = physics._build_space(state)
    _settle_into_sleep(space, a + b)
    kicker = a[-1]
    start_x = kicker.position.x
    physics._launch_kicker(state, kicker, 2, 0, 80, True)
    assert not kicker.is_sleeping
    assert kicker.velocity.x == pytest.approx(80 * physics._get_player_kick_vel(physics.DEFAULT_STATS))
    trajectory, _, _ = physics._sim(space, a, b, ball, referee, 2, True, ball_pivot=pivot)
    assert trajectory[-1]["a"][-1]["x"] > start_x


def test_sleeping_player_snapshots_cannot_mutate_later_frames_or_cached_coordinates():
    state = physics.new_soccer_state()
    space, a, b, _, _, _ = physics._build_space(state)
    _settle_into_sleep(space, a + b)
    cache = {}
    first = physics._player_positions(a, cache)
    second = physics._player_positions(a, cache)
    expected = copy.deepcopy(second)
    first[0]["x"] += 1000
    assert second == expected
    assert physics._player_positions(a, cache) == expected
    second[-1]["y"] -= 1000
    assert physics._player_positions(a, cache) == expected
